# Nazwy zestawów, mapa zmian i wyjątki od równoległości: tools/test_suites.py.
TEST_SUITES := $(shell /usr/bin/python3 tools/test_runner.py --list-suites)
TEST_JOB_ARGS = $(if $(strip $(JOBS)),--jobs "$(JOBS)")
TEST_OUTPUT_ARGS = $(if $(strip $(TEST_OUTPUT)),--output "$(TEST_OUTPUT)")
BASE ?= master

.PHONY: test-changed test-benchmark $(addprefix test-,$(TEST_SUITES))

$(addprefix test-,$(TEST_SUITES)):
	@/usr/bin/python3 tools/test_runner.py --dirs $(TEST_DIRS) --suite $(@:test-%=%) $(TEST_JOB_ARGS) $(TEST_OUTPUT_ARGS)

test-changed:
	@/usr/bin/python3 tools/test_runner.py --dirs $(TEST_DIRS) --changed --base "$(BASE)" $(TEST_JOB_ARGS) $(TEST_OUTPUT_ARGS)

test-benchmark:
	@/usr/bin/python3 tools/test_benchmark.py --dirs $(TEST_DIRS) $(TEST_JOB_ARGS) $(TEST_OUTPUT_ARGS)
