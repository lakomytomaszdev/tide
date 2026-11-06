#!/bin/bash
# PT-189: najpierw regresje z ostatniego pełnego przebiegu, potem cały zestaw.
# Uruchamiane w worktree poza piaskownicą kroku (prywatny D-Bus/HTTP i zagnieżdżony bwrap).
set -euo pipefail

python3 -m unittest \
    daemon.tests.test_cardflow_youtrack.YouTrackFlowTest.test_offline_result_comment_does_not_block_next_ticket \
    daemon.tests.test_cardflow_limits.TransferFlowTest.test_limit_without_any_choice_resumes_by_itself_after_the_reset \
    daemon.tests.test_cardflow_plan_integration.PlanFlowTest.test_failed_plan_commit_can_be_retried_without_new_ticket_numbers \
    daemon.tests.test_cardflow_step_decisions.StepDecisionTest.test_long_command_is_shown_in_full_and_granted_as_its_family \
    daemon.tests.test_stream_mode.StreamModeTest.test_stopping_the_service_in_stream_releases_everything

exec make test
