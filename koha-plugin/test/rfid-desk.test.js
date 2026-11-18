// node --test koha-plugin/test   (also run by tests/test_plugin.py)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const rd = require("../Koha/Plugin/Org/Middelburg/RFIDDesk/rfid-desk.js");

const item = (barcode, extra) => Object.assign(
    { barcode, title: "T " + barcode, onloan: false, checked_out_to: null, holds: 0 }, extra || {});
const tag = (barcode, candidates) => ({ barcode, candidates: candidates || [barcode] });

test("resolveTag takes the first candidate Koha knows (exact first)", () => {
    const lookup = { "33228100096617": null, "3322810009661": item("3322810009661") };
    assert.equal(rd.resolveTag(tag("33228100096617", ["33228100096617", "3322810009661"]), lookup).barcode,
                 "3322810009661");
    const both = { "1": item("1"), "2": item("2") };
    assert.equal(rd.resolveTag(tag("1", ["1", "2"]), both).barcode, "1");
});

test("checkout decisions", () => {
    assert.equal(rd.decide("checkout", item("1"), 64).action, "do");
    assert.equal(rd.decide("checkout", item("1", { onloan: true, checked_out_to: 64 }), "64").action, "done");
    assert.match(rd.decide("checkout", item("1", { onloan: true, checked_out_to: 9 }), 64).reason, /another patron/);
    assert.match(rd.decide("checkout", item("1", { holds: 2 }), 64).reason, /holds/);
    assert.equal(rd.decide("checkout", null, 64).action, "skip");
    assert.equal(rd.decide("checkout", undefined, 64).action, "wait");
});

test("checkin decisions", () => {
    assert.equal(rd.decide("checkin", item("1", { onloan: true, checked_out_to: 9 })).action, "do");
    assert.equal(rd.decide("checkin", item("1")).action, "skip");
});

test("planPad lists each item once and only queues what applies", () => {
    const tags = [
        tag("33228100096617", ["33228100096617", "3322810009661"]),
        tag("3322810009661", ["3322810009661", "33228100096617"]),   // same book, other form
        tag("2"), tag("3"), tag("4"),
    ];
    const lookup = {
        "33228100096617": null, "3322810009661": item("3322810009661"),
        "2": item("2", { onloan: true, checked_out_to: 9 }), "3": null,
        // "4" not looked up yet
    };
    const p = rd.planPad("checkout", tags, lookup, 64);
    assert.deepEqual(p.todo, ["3322810009661"]);
    assert.deepEqual(p.rows.map(r => r.action), ["do", "skip", "skip", "wait"]);
});

test("needLookup asks only for unknown or stale barcodes", () => {
    const now = 100000;
    const cache = { a: { at: now - 1000, item: null }, b: { at: now - 60000, item: null } };
    assert.deepEqual(rd.needLookup([tag("a", ["a", "b", "c"]), tag("c")], cache, now), ["b", "c"]);
});

test("blockedBy recognises Koha's questions", () => {
    const doc = (...present) => ({ querySelector: (s) => present.includes(s) ? {} : null });
    assert.match(rd.blockedBy(doc("#circ_needsconfirmation"), "checkout"), /decision/);
    assert.match(rd.blockedBy(doc("#circ_impossible"), "checkout"), /could not/);
    assert.equal(rd.blockedBy(doc(".modal.block"), "checkout"), null);
    assert.match(rd.blockedBy(doc(".modal.block"), "checkin"), /attention/);
    assert.match(rd.blockedBy(doc(".modal.noblock"), "checkin"), /attention/);
    assert.match(rd.blockedBy(doc(".dialog.alert.audio-alert-warning"), "checkin"), /message/);
    assert.equal(rd.blockedBy(doc(), "checkin"), null);
});

test("queue: submits in order, waits when Koha asks, continues after", () => {
    const q = rd.newQueue("checkout", 64, ["a", "b"], 0);
    assert.deepEqual(rd.nextStep(q, null, 1), { type: "submit", barcode: "a" });
    assert.deepEqual(rd.nextStep(q, "Koha needs your decision", 2), { type: "wait" });
    assert.equal(q.next, 1);                                   // still waiting on "a"'s question
    q.ack = true;                                              // librarian pressed Continue
    assert.deepEqual(rd.nextStep(q, "still shown", 3), { type: "submit", barcode: "b" });
    assert.equal(q.ack, false);
    assert.deepEqual(rd.nextStep(q, null, 4), { type: "done" });
});

test("queue expires", () => {
    const q = rd.newQueue("checkin", null, ["a"], 0);
    assert.deepEqual(rd.nextStep(q, null, rd.QUEUE_MAX_AGE_MS + 1), { type: "expired" });
});

test("verify checks Koha's final state", () => {
    const lookup = { a: item("a", { onloan: true, checked_out_to: 64 }), b: item("b"),
                     c: item("c", { onloan: true, checked_out_to: 9 }) };
    assert.deepEqual(rd.verify("checkout", ["a", "b", "c"], lookup, "64"), { ok: ["a"], notOk: ["b", "c"] });
    assert.deepEqual(rd.verify("checkin", ["a", "b"], lookup), { ok: ["b"], notOk: ["a"] });
});

test("panel only shows on a PC that uses RFID", () => {
    const now = 1e12, day = 24 * 60 * 60 * 1000;
    assert.equal(rd.panelVisible(null, null, false, now), false);          // never seen, first poll pending
    assert.equal(rd.panelVisible(false, null, false, now), false);         // no agent installed
    assert.equal(rd.panelVisible(true, null, false, now), true);           // agent answers
    assert.equal(rd.panelVisible(false, String(now - day), false, now), true);   // agent stopped: say so
    assert.equal(rd.panelVisible(false, String(now - rd.SEEN_MAX_AGE_MS - 1), false, now), false);
    assert.equal(rd.panelVisible(false, "junk", false, now), false);
    assert.equal(rd.panelVisible(false, null, true, now), true);           // queue running
});
