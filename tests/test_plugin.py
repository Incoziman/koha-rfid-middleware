"""Checks on the Koha plugin that can run without a Koha server.

- the page script's logic tests (node, if installed)
- the Perl modules compile (perl, if installed) against small stand-ins for
  the Koha and Mojolicious modules they use
- every REST route points at a controller method that exists
- the .kpz builds with the files Koha needs
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from koha_rfid import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "koha-plugin")
BUNDLE = os.path.join(PLUGIN, "Koha", "Plugin", "Org", "Middelburg", "RFIDDesk")
MODULE = BUNDLE + ".pm"

PERL_STUBS = {
    "Modern/Perl.pm": "package Modern::Perl; sub import { strict->import; warnings->import } 1;",
    "Koha/Plugins/Base.pm": "package Koha::Plugins::Base; sub new { bless $_[1], $_[0] } 1;",
    "Mojo/JSON.pm": ("package Mojo::JSON; use Exporter 'import'; our @EXPORT_OK = qw(decode_json encode_json);"
                     " sub decode_json {} sub encode_json {} sub true {1} sub false {0} 1;"),
    "Mojo/Base.pm": ("package Mojo::Base; sub import { my ($c, $base) = @_; my $caller = caller;"
                     " no strict 'refs'; push @{\"${caller}::ISA\"}, $base if $base;"
                     " strict->import; warnings->import } 1;"),
    "Mojolicious/Controller.pm": "package Mojolicious::Controller; 1;",
    "Koha/Items.pm": "package Koha::Items; 1;",
    "Koha/Holds.pm": "package Koha::Holds; 1;",
    "Koha/Biblios.pm": "package Koha::Biblios; 1;",
}


class Plugin(unittest.TestCase):
    def test_versions_match(self):
        with open(MODULE, encoding="utf-8") as fh:
            m = re.search(r"our \$VERSION\s*=\s*'([^']+)'", fh.read())
        self.assertEqual(m.group(1), __version__)

    def test_routes_point_at_controller_methods(self):
        with open(os.path.join(BUNDLE, "openapi.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
        with open(os.path.join(BUNDLE, "Controller.pm"), encoding="utf-8") as fh:
            controller = fh.read()
        for path, ops in spec.items():
            for op in ops.values():
                target = op["x-mojo-to"]
                self.assertTrue(target.startswith("Org::Middelburg::RFIDDesk::Controller#"), target)
                self.assertRegex(controller, r"\nsub " + target.split("#")[1] + r" \{", target)
                self.assertIn("x-koha-authorization", op, path)

    def test_kpz_contents(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_plugin",
                                                      os.path.join(ROOT, "deploy", "build_plugin.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            with zipfile.ZipFile(mod.build(d)) as z:
                names = set(z.namelist())
        base = "Koha/Plugin/Org/Middelburg/RFIDDesk"
        for f in (".pm", "/Controller.pm", "/openapi.json", "/rfid-desk.js", "/configure.tt"):
            self.assertIn(base + f, names)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_page_script(self):
        r = subprocess.run(["node", "--test", os.path.join(PLUGIN, "test", "rfid-desk.test.js")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(shutil.which("perl"), "perl not installed")
    def test_perl_compiles(self):
        with tempfile.TemporaryDirectory() as stubs:
            for rel, code in PERL_STUBS.items():
                p = os.path.join(stubs, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(code + "\n")
            for pm in (MODULE, os.path.join(BUNDLE, "Controller.pm")):
                r = subprocess.run(["perl", "-I", stubs, "-I", PLUGIN, "-c", pm],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
