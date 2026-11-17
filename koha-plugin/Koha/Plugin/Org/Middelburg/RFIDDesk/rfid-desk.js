/*
 * RFID desk: staff interface script for the Koha RFID desk plugin.
 *
 * Talks to the desk agent on this PC (koha_rfid.agent, window.RFIDDesk.agentUrl)
 * and to the plugin's read-only REST routes (window.RFIDDesk.api).
 *
 *  - Check out (circ/circulation.pl with a patron) and Check in (circ/returns.pl):
 *    a panel lists the books on the RFID pad. "Check out N books" / "Check in N
 *    books" puts their barcodes in a queue and submits them ONE BY ONE THROUGH
 *    KOHA'S OWN FORM, so Koha records the logged-in librarian and branch and
 *    applies every rule. When Koha asks a question (hold, limit, transfer...)
 *    the queue waits for the librarian, then carries on.
 *  - Bibliographic records: "RFID labels" prints labels on the agent's printer.
 *
 * The pure logic at the top is unit-tested with node (koha-plugin/test/).
 */
(function (root, factory) {
    "use strict";
    var lib = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = lib;                               // node tests
    } else if (root && root.document && root.RFIDDesk) {
        var go = function () { lib.start(root, root.RFIDDesk); };
        if (root.document.readyState === "loading") {
            root.document.addEventListener("DOMContentLoaded", go);
        } else {
            go();
        }
    }
})(typeof window !== "undefined" ? window : null, function () {
    "use strict";

    var QUEUE_KEY = "rfiddesk.queue";
    var QUEUE_MAX_AGE_MS = 15 * 60 * 1000;
    var POLL_MS = 1000;
    var CACHE_MS = 15000;

    // ======================================================================
    // Pure logic
    // ======================================================================

    /** First candidate barcode Koha knows. lookup: {barcode: item|null}. */
    function resolveTag(tag, lookup) {
        var c = tag.candidates && tag.candidates.length ? tag.candidates : [tag.barcode];
        for (var i = 0; i < c.length; i++) {
            if (lookup[c[i]]) { return lookup[c[i]]; }
        }
        return null;
    }

    /** What to do with one item: {action: "do"|"done"|"skip"|"wait", reason}. */
    function decide(mode, item, borrowernumber) {
        if (item === undefined) { return { action: "wait", reason: "looking up..." }; }
        if (!item) { return { action: "skip", reason: "not in Koha" }; }
        var bn = borrowernumber == null ? null : Number(borrowernumber);
        if (mode === "checkout") {
            if (item.onloan && item.checked_out_to === bn) {
                return { action: "done", reason: "checked out to this patron" };
            }
            if (item.onloan) {
                return { action: "skip", reason: "on loan to another patron - check it in first" };
            }
            if (item.holds > 0) { return { action: "do", reason: "has holds - Koha will ask" }; }
            return { action: "do", reason: "available" };
        }
        if (!item.onloan) { return { action: "skip", reason: "not on loan" }; }
        return { action: "do", reason: "on loan" };
    }

    /**
     * Rows for the panel and the barcodes to submit.
     * tags: agent /pad tags; lookup: {barcode: item|null|undefined}.
     * Two tags for the same item are listed once.
     */
    function planPad(mode, tags, lookup, borrowernumber) {
        var rows = [], todo = [], seen = {};
        tags.forEach(function (tag) {
            var known = (tag.candidates || [tag.barcode]).every(function (b) { return b in lookup; });
            var item = resolveTag(tag, lookup);
            if (!item && !known) { item = undefined; }
            var key = item ? "i" + item.barcode : "t" + tag.barcode;
            if (seen[key]) { return; }
            seen[key] = true;
            var d = decide(mode, item, borrowernumber);
            rows.push({ tag: tag, item: item, action: d.action, reason: d.reason });
            if (d.action === "do") { todo.push(item.barcode); }
        });
        return { rows: rows, todo: todo };
    }

    /** Barcodes (all candidates) not yet looked up or older than the cache. */
    function needLookup(tags, cache, now) {
        var out = [];
        tags.forEach(function (t) {
            (t.candidates || [t.barcode]).forEach(function (b) {
                var hit = cache[b];
                if ((!hit || now - hit.at > CACHE_MS) && out.indexOf(b) < 0) { out.push(b); }
            });
        });
        return out;
    }

    /** Is Koha waiting for the librarian? Returns a message, or null. */
    function blockedBy(doc, mode) {
        var q = function (sel) { return doc.querySelector(sel); };
        if (mode === "checkout") {
            if (q("#circ_needsconfirmation")) { return "Koha needs your decision on this item"; }
            if (q("#circ_impossible")) { return "Koha could not check this item out"; }
            return null;
        }
        if (q(".modal.block") || q(".modal.noblock")) {
            return "Koha needs your attention on this item (hold, transfer or branch)";
        }
        if (q(".dialog.alert.audio-alert-warning")) { return "Koha has a check-in message for this item"; }
        return null;
    }

    function newQueue(mode, borrowernumber, barcodes, now) {
        return { mode: mode, bn: borrowernumber == null ? null : String(borrowernumber),
                 barcodes: barcodes.slice(), next: 0, started: now, ack: false };
    }

    /**
     * The next step for a queue, given whether Koha is waiting on the librarian:
     * {type: "submit", barcode} | {type: "wait"} | {type: "done"} | {type: "expired"}.
     * Mutates q (advances next, clears ack).
     */
    function nextStep(q, blocked, now) {
        if (now - q.started > QUEUE_MAX_AGE_MS) { return { type: "expired" }; }
        if (blocked && !q.ack) { return { type: "wait" }; }
        q.ack = false;
        if (q.next >= q.barcodes.length) { return { type: "done" }; }
        var barcode = q.barcodes[q.next];
        q.next += 1;
        return { type: "submit", barcode: barcode };
    }

    /** After the queue: how many items ended up as intended. */
    function verify(mode, barcodes, lookup, borrowernumber) {
        var bn = borrowernumber == null ? null : Number(borrowernumber);
        var ok = [], notOk = [];
        barcodes.forEach(function (b) {
            var it = lookup[b];
            var good = it && (mode === "checkout" ? it.onloan && it.checked_out_to === bn : !it.onloan);
            (good ? ok : notOk).push(b);
        });
        return { ok: ok, notOk: notOk };
    }

    // ======================================================================
    // Browser
    // ======================================================================

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (k === "text") { e.textContent = attrs[k]; }
            else if (k === "class") { e.className = attrs[k]; }
            else if (k.slice(0, 2) === "on") { e.addEventListener(k.slice(2), attrs[k]); }
            else { e.setAttribute(k, attrs[k]); }
        });
        (children || []).forEach(function (c) { if (c) { e.appendChild(c); } });
        return e;
    }

    function timeoutFetch(url, opts, ms) {
        var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
        var t = ctl ? setTimeout(function () { ctl.abort(); }, ms) : null;
        opts = opts || {};
        if (ctl) { opts.signal = ctl.signal; }
        return fetch(url, opts).finally(function () { if (t) { clearTimeout(t); } });
    }

    function makeKoha(cfg) {
        var cache = {};
        function getJSON(path) {
            return timeoutFetch(cfg.api + path, { credentials: "same-origin",
                headers: { Accept: "application/json" } }, 10000).then(function (r) {
                if (!r.ok) { throw new Error("Koha API " + r.status); }
                return r.json();
            });
        }
        return {
            cache: cache,
            lookup: function () {
                var out = {};
                Object.keys(cache).forEach(function (b) { out[b] = cache[b].item; });
                return out;
            },
            fetchItems: function (barcodes) {
                if (!barcodes.length) { return Promise.resolve(); }
                var qs = barcodes.map(function (b) { return "barcode=" + encodeURIComponent(b); }).join("&");
                return getJSON("/items?" + qs).then(function (data) {
                    var now = Date.now();
                    data.items.forEach(function (it) {
                        cache[it.requested] = { at: now, item: it.found ? it : null };
                    });
                });
            },
            forget: function (barcodes) { barcodes.forEach(function (b) { delete cache[b]; }); },
            biblioItems: function (bn) { return getJSON("/biblios/" + encodeURIComponent(bn) + "/items"); }
        };
    }

    function makeAgent(cfg) {
        return {
            pad: function () {
                return timeoutFetch(cfg.agentUrl + "/pad", { cache: "no-store" }, 2500)
                    .then(function (r) { if (!r.ok) { throw new Error("agent " + r.status); } return r.json(); });
            },
            print: function (items) {
                return timeoutFetch(cfg.agentUrl + "/print", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ items: items })
                }, 60000).then(function (r) {
                    return r.json().then(function (body) {
                        if (!r.ok) { throw new Error(body.error || ("agent " + r.status)); }
                        return body;
                    });
                });
            }
        };
    }

    function addStyles() {
        if (document.getElementById("rfiddesk-style")) { return; }
        var css = [
            "#rfiddesk{border:1px solid #ccc;border-radius:4px;padding:8px 10px;margin:0 0 12px;background:#fafafa}",
            "#rfiddesk h3{font-size:15px;margin:0 0 6px}",
            "#rfiddesk .rd-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;background:#999}",
            "#rfiddesk .rd-dot.ok{background:#3a3}#rfiddesk .rd-dot.warn{background:#e90}",
            "#rfiddesk table{width:100%;font-size:13px;margin:4px 0 8px}",
            "#rfiddesk td{padding:2px 4px;vertical-align:top}",
            "#rfiddesk tr.skip{color:#999}#rfiddesk tr.done{color:#3a3}",
            "#rfiddesk .rd-msg{margin:6px 0;padding:6px;border-radius:3px}",
            "#rfiddesk .rd-msg.wait{background:#fff3cd}#rfiddesk .rd-msg.good{background:#dff0d8}#rfiddesk .rd-msg.bad{background:#f2dede}"
        ].join("\n");
        document.head.appendChild(el("style", { id: "rfiddesk-style", text: css }));
    }

    function loadQueue(storage) {
        try { return JSON.parse(storage.getItem(QUEUE_KEY) || "null"); } catch (e) { return null; }
    }
    function saveQueue(storage, q) {
        try { if (q) { storage.setItem(QUEUE_KEY, JSON.stringify(q)); } else { storage.removeItem(QUEUE_KEY); } }
        catch (e) { /* storage unavailable: queue can't survive reloads */ }
    }

    // ---- check-out / check-in panel ----------------------------------------
    function desk(win, cfg, mode) {
        var doc = win.document;
        var form = mode === "checkout" ? doc.getElementById("mainform") : doc.getElementById("checkin-form");
        var input = form && form.querySelector("#barcode");
        var bnInput = doc.getElementById("borrowernumber");
        var bn = mode === "checkout" && bnInput ? bnInput.value : null;
        if (!form || !input || (mode === "checkout" && !bn)) { return; }

        var storage = win.sessionStorage;
        var koha = makeKoha(cfg), agent = makeAgent(cfg);
        var verb = mode === "checkout" ? "Check out" : "Check in";
        var state = { tags: [], agentOk: null, readerOk: null, plan: { rows: [], todo: [] }, busy: false };

        addStyles();
        var dot = el("span", { class: "rd-dot" });
        var statusText = el("span", { text: "Connecting to the RFID reader..." });
        var tbody = el("tbody");
        var msg = el("div", { class: "rd-msg", style: "display:none" });
        var goBtn = el("button", { type: "button", class: "btn btn-primary btn-sm", text: verb, onclick: start });
        var stopBtn = el("button", { type: "button", class: "btn btn-default btn-sm", text: "Stop",
            style: "display:none;margin-left:6px", onclick: stop });
        var contBtn = el("button", { type: "button", class: "btn btn-default btn-sm", text: "Continue",
            style: "display:none;margin-left:6px", onclick: acknowledge });
        var panel = el("div", { id: "rfiddesk" }, [
            el("h3", {}, [dot, el("span", { text: "RFID pad " }), statusText]),
            el("table", {}, [tbody]), msg, goBtn, stopBtn, contBtn
        ]);
        form.parentNode.insertBefore(panel, form);

        function say(text, kind) {
            msg.textContent = text || "";
            msg.className = "rd-msg " + (kind || "");
            msg.style.display = text ? "" : "none";
        }

        function render() {
            var q = loadQueue(storage);
            var running = !!(q && q.mode === mode);
            if (state.agentOk === false) {
                dot.className = "rd-dot"; statusText.textContent = "- RFID agent not running on this PC (normal barcode scanning still works)";
            } else if (state.readerOk === false) {
                dot.className = "rd-dot warn"; statusText.textContent = "- reader not connected";
            } else if (state.agentOk) {
                dot.className = "rd-dot ok";
                var n = state.plan.rows.length;
                statusText.textContent = "- " + n + (n === 1 ? " book" : " books") + " on the pad";
            }
            while (tbody.firstChild) { tbody.removeChild(tbody.firstChild); }
            state.plan.rows.forEach(function (r) {
                var title = r.item ? (r.item.title || "") : "";
                tbody.appendChild(el("tr", { class: r.action }, [
                    el("td", { text: r.action === "do" ? "→" : r.action === "done" ? "✓" : r.action === "wait" ? "…" : "–" }),
                    el("td", { text: r.item ? r.item.barcode : r.tag.barcode }),
                    el("td", { text: title }),
                    el("td", { text: r.reason })
                ]));
            });
            var n2 = state.plan.todo.length;
            goBtn.textContent = verb + " " + n2 + (n2 === 1 ? " book" : " books");
            goBtn.disabled = running || n2 === 0 || state.busy;
            stopBtn.style.display = running ? "" : "none";
        }

        function poll() {
            var q = loadQueue(storage);
            if (q && q.mode === mode) { return; }            // queue running: page will reload
            agent.pad().then(function (pad) {
                state.agentOk = true;
                state.readerOk = !!pad.reader_connected;
                state.tags = pad.tags || [];
                var need = needLookup(state.tags, koha.cache, Date.now());
                return koha.fetchItems(need);
            }, function () {
                state.agentOk = false; state.tags = [];
            }).then(function () {
                state.plan = planPad(mode, state.tags, koha.lookup(), bn);
                render();
            }, function (e) {
                say("Could not look items up in Koha: " + e.message, "bad");
            });
        }

        function submit(barcode) {
            input.disabled = false;
            input.value = barcode;
            if (win.jQuery) { win.jQuery(form).submit(); }
            else if (form.requestSubmit) { form.requestSubmit(); }
            else { form.submit(); }
        }

        function step() {
            var q = loadQueue(storage);
            if (!q || q.mode !== mode) { return false; }
            if (mode === "checkout" && q.bn !== String(bn)) {   // different patron now: never carry over
                saveQueue(storage, null);
                say("The RFID queue was for another patron and has been stopped.", "bad");
                return false;
            }
            var blocked = blockedBy(doc, mode);
            var s = nextStep(q, blocked, Date.now());
            if (s.type === "expired") {
                saveQueue(storage, null); say("The RFID queue was too old and has been stopped.", "bad");
                return false;
            }
            if (s.type === "wait") {
                saveQueue(storage, q);
                var cur = q.barcodes[q.next - 1];
                say(blocked + " (" + cur + ", item " + q.next + " of " + q.barcodes.length +
                    "). Deal with it in Koha's message; the RFID queue carries on afterwards. " +
                    "If there is nothing to click, press Continue.", "wait");
                contBtn.style.display = "";
                if (win.jQuery) {     // check-in modals closed without a page reload
                    win.jQuery(".modal").one("hidden.bs.modal", function () { acknowledge(); });
                }
                render();
                return true;
            }
            if (s.type === "done") {
                saveQueue(storage, null);
                finish(q);
                return false;
            }
            saveQueue(storage, q);
            say(verb + ": item " + q.next + " of " + q.barcodes.length + " (" + s.barcode + ")...", "wait");
            render();
            submit(s.barcode);
            return true;
        }

        function finish(q) {
            koha.forget(q.barcodes);
            koha.fetchItems(q.barcodes).then(function () {
                var v = verify(mode, q.barcodes, koha.lookup(), bn);
                var done = mode === "checkout" ? "checked out" : "checked in";
                if (!v.notOk.length) {
                    say("Done: all " + v.ok.length + " " + (v.ok.length === 1 ? "book" : "books") + " " + done + ".", "good");
                } else {
                    say(v.ok.length + " of " + q.barcodes.length + " " + done + ". Not " + done + ": " +
                        v.notOk.join(", ") + " (see Koha's messages).", "bad");
                }
            }, function (e) { say("Finished, but could not confirm the result in Koha: " + e.message, "bad"); })
              .then(poll);
        }

        function start() {
            var todo = state.plan.todo.slice();
            if (!todo.length) { return; }
            saveQueue(storage, newQueue(mode, bn, todo, Date.now()));
            step();
        }

        function stop() {
            saveQueue(storage, null);
            contBtn.style.display = "none";
            say("Stopped. Items already submitted stay as Koha recorded them.", "bad");
            poll();
        }

        function acknowledge() {
            var q = loadQueue(storage);
            if (!q) { return; }
            q.ack = true;
            saveQueue(storage, q);
            contBtn.style.display = "none";
            step();
        }

        render();
        if (!step()) { poll(); }
        win.setInterval(poll, POLL_MS);
    }

    // ---- RFID labels on bibliographic records -------------------------------
    function labelsButton(win, cfg) {
        var doc = win.document;
        var m = /[?&]biblionumber=(\d+)/.exec(win.location.search);
        if (!m) { return; }
        var biblionumber = m[1];
        var koha = makeKoha(cfg), agent = makeAgent(cfg);
        addStyles();

        var btn = el("button", { type: "button", class: "btn btn-default", id: "rfiddesk-labels",
            onclick: open }, [el("i", { class: "fa fa-tag" }), document.createTextNode(" RFID labels")]);
        var toolbar = doc.getElementById("toolbar");
        if (toolbar) { toolbar.appendChild(el("div", { class: "btn-group" }, [btn])); }
        else {
            var main = doc.querySelector("main") || doc.body;
            main.insertBefore(btn, main.firstChild);
        }

        var box = null;
        function open() {
            if (box) { box.parentNode.removeChild(box); }
            var body = el("div", { text: "Loading items..." });
            box = el("div", { id: "rfiddesk", style: "position:fixed;top:60px;right:20px;z-index:2000;width:520px;max-height:80vh;overflow:auto;box-shadow:0 4px 16px rgba(0,0,0,.25)" }, [
                el("h3", { text: "Print RFID labels" }), body,
                el("button", { type: "button", class: "btn btn-default btn-sm", text: "Close",
                    onclick: function () { box.parentNode.removeChild(box); box = null; } })
            ]);
            doc.body.appendChild(box);
            koha.biblioItems(biblionumber).then(function (data) { show(body, data); },
                function (e) { body.textContent = "Could not load the items: " + e.message; });
        }

        function show(body, data) {
            body.textContent = "";
            var tb = el("tbody");
            var boxes = [];
            data.items.forEach(function (it) {
                var cb = el("input", { type: "checkbox" });
                cb.checked = !!it.encodable;
                cb.disabled = !it.encodable;
                cb._item = it;
                boxes.push(cb);
                tb.appendChild(el("tr", { class: it.encodable ? "" : "skip" }, [
                    el("td", {}, [cb]),
                    el("td", { text: it.barcode || "(no barcode)" }),
                    el("td", { text: [it.homebranch, it.callnumber].filter(Boolean).join(" ") }),
                    el("td", { text: it.encodable ? "" : "can't go on a tag (not 1-22 digits)" })
                ]));
            });
            var result = el("div", { class: "rd-msg", style: "display:none" });
            var printBtn = el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Print selected",
                onclick: function () {
                    var items = boxes.filter(function (b) { return b.checked; })
                        .map(function (b) { return { barcode: b._item.barcode, title: data.title || "" }; });
                    if (!items.length) { return; }
                    printBtn.disabled = true;
                    result.style.display = ""; result.className = "rd-msg wait";
                    result.textContent = "Printing " + items.length + " label(s)...";
                    agent.print(items).then(function (r) {
                        var bad = r.results.filter(function (x) { return x.status !== "OK"; });
                        result.className = "rd-msg " + (bad.length ? "bad" : "good");
                        result.textContent = bad.length
                            ? bad.map(function (x) { return x.barcode + ": " + x.status; }).join("; ") +
                              ". Reprint those labels; void the failed ones."
                            : "Printed " + r.results.length + " label(s); every tag read back correctly.";
                    }, function (e) {
                        result.className = "rd-msg bad";
                        result.textContent = "Not printed: " + e.message;
                    }).then(function () { printBtn.disabled = false; });
                } });
            body.appendChild(el("p", { text: data.title || "" }));
            body.appendChild(el("table", {}, [tb]));
            body.appendChild(printBtn);
            body.appendChild(result);
        }
    }

    function start(win, cfg) {
        var path = win.location.pathname;
        try {
            if (cfg.checkout && /\/circ\/circulation\.pl$/.test(path)) { desk(win, cfg, "checkout"); }
            else if (cfg.checkin && /\/circ\/returns\.pl$/.test(path)) { desk(win, cfg, "checkin"); }
            else if (cfg.labels && /\/(catalogue\/(detail|moredetail)|cataloguing\/additem)\.pl$/.test(path)) {
                labelsButton(win, cfg);
            }
        } catch (e) {
            if (win.console) { win.console.error("RFID desk:", e); }
        }
    }

    return {
        resolveTag: resolveTag, decide: decide, planPad: planPad, needLookup: needLookup,
        blockedBy: blockedBy, newQueue: newQueue, nextStep: nextStep, verify: verify,
        QUEUE_MAX_AGE_MS: QUEUE_MAX_AGE_MS, start: start
    };
});
