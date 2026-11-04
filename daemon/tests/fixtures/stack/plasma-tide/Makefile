TEST_DIRS := daemon/tests

-include mk/*.mk

test:
	@for dir in $(TEST_DIRS); do QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s "$$dir" -t . || exit 1; done

lint:
	@python3 -m pyflakes daemon
