"""PT-318, review r3: opcje interpreterów nie ukrywają kodu w kontrakcie testów."""
import unittest

from tide_daemon.cardflow import mergecheck


START = "a" * 40


class InterpreterContractTest(unittest.TestCase):
    def assert_unavailable(self, contract):
        self.assertEqual(mergecheck.select_command(contract, {"test-changed", "test"}, START),
                         {"source": "contract", "reason": "contract-not-single"})

    def test_review_examples_cannot_supply_inline_code(self):
        # To wyłącznie dane dla selektora: żaden interpreter ani kod kontraktu nie jest uruchamiany.
        for contract in (
            "python3 -W ignore -c 'import os; os.system(\"echo {base} && make test\")'",
            "python3 -X dev -c 'import os; os.system(\"echo {base} && make test\")'",
            "perl -we 'system(\"echo {base} && make test\")'",
            "node --eval='require(\"child_process\").execSync(\"echo {base} && make test\")'",
        ):
            with self.subTest(contract=contract):
                self.assert_unavailable(contract)

    def test_unknown_options_and_other_inline_forms_are_rejected(self):
        for contract in (
            "/usr/bin/python3 -Wignore -Xdev -Sc 'print(1)'",
            "python3 --unknown value -c 'print(1)'",
            "python3 -W ignore -X dev -cprint(1)",
            "perl -I lib -we 'print 1'", "ruby -we 'puts 1'",
            "node --print='1'", "node -pe '1'", "node -m 'code'",
            "php -r 'echo 1;'", "lua -e 'print(1)'", "tclsh -unknown value",
            "python3", "python3 -", "python3 -W", "python3 -X dev",
            "python3 -m", "python3 --", "python3 -- -",
        ):
            with self.subTest(contract=contract):
                self.assert_unavailable(contract)

    def test_module_and_script_arguments_remain_opaque(self):
        for contract in (
            "/usr/bin/python3 -m pytest -p no:cacheprovider --base {base}",
            "python3 tools/test_runner.py -c fast --base {base}",
            "python3 -W ignore -X dev -m pytest -p no:cacheprovider --base {base}",
            "python3 -B -E -I -s -S -u tools/test_runner.py --base {base}",
            "python3 -- tools/test_runner.py -c fast --base {base}",
            "perl tools/tests.pl --base {base}", "node tools/tests.js --base {base}",
        ):
            with self.subTest(contract=contract):
                self.assertEqual(mergecheck.select_command(contract, {"test-changed"}, START),
                                 {"command": contract.replace("{base}", START), "source": "contract"})
