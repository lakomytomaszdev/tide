"""PT-171: konfiguracja Dockera i obrazy tylko przez Incusa, bez Dockera hosta."""
from unittest import mock

from tide_daemon.box import profile as bp, state as bs, templates
from tide_daemon.cardflow import envconsent
from tide_daemon.tasks import stack

from .test_box_builder import BoxCase, PROFILE, PROXY


class DockerTest(BoxCase):
    def test_enabled_and_stack_enable_nesting_and_intercepts_before_templates(self):
        for profile in ({**PROFILE, "stacks": [], "docker": {"enabled": True}},
                        {**PROFILE, "stacks": ["docker"], "docker": {"images": []}}):
            self.builder().build("plasma-tide", profile)
            argvs = self.incus_calls()
            init = next(a for a in argvs if a[0] == "init")
            for key in ("security.nesting", "security.syscalls.intercept.mknod",
                        "security.syscalls.intercept.setxattr"):
                self.assertIn(key + "=true", init)
            template = next(a for a in argvs if a[0] == "exec" and "/opt/tide/stacks/docker.sh" in a)
            self.assertLess(argvs.index(init), argvs.index(template))

    def test_without_docker_no_nesting_or_intercepts(self):
        self.builder().build("plasma-tide", {"tide-box": 1})
        init = next(a for a in self.incus_calls() if a[0] == "init")
        self.assertFalse(any("security.nesting" in a or "security.syscalls" in a for a in init))

    def test_template_and_profile_images_pull_through_proxy_and_failure_warns(self):
        builder = self.builder()
        original = builder.incus.exec

        def run(name, command, env=None, *args, **kwargs):
            code = original(name, command, env, *args, **kwargs)
            return 7 if command == ["docker", "pull", "postgres:16"] else code

        with mock.patch.object(builder.incus, "exec", side_effect=run):
            result = builder.build("plasma-tide", PROFILE)
        pulls = [a for a in self.incus_calls() if a[0] == "exec" and "docker" in a and "pull" in a]
        self.assertEqual([a[-1] for a in pulls], ["node:20", "testcontainers/ryuk:0.11.0", "postgres:16"])
        for argv in pulls:
            self.assertIn("plasma-tide", argv)
            self.assertIn("user-1000", argv)
            self.assertIn("HTTPS_PROXY=" + PROXY, argv)
        self.assertFalse(any("DOCKER_HOST" in word for a in self.incus_calls() for word in a))
        self.assertEqual(result.failed_images, ["postgres:16"])
        self.assertTrue(result.warning)
        self.assertEqual(bs.journal_state("plasma-tide", "plasma-tide"), (bs.CLEAN, True))
        self.assertIn("snapshot", result.steps)

    def test_docker_risk_is_yellow_and_explains_credentials(self):
        for profile in ({"docker": {"enabled": True}}, {"stacks": ["docker"]}):
            item = next(i for i in envconsent._profile_items(profile, {}, "projekt") if i["id"] == "docker")
            self.assertEqual(item["risk"], bp.YELLOW)
            for text in ("K1", "grupie docker", "roota", "denyRead", "poświadczeń", "K3"):
                self.assertIn(text, item["note"])
        # Ta sama treść trafia do propozycji profilu, a nie tylko sekcji zatwierdzonego profilu.
        proposed = stack.propose_profile(stack.StackReport(stacks=["docker"]))
        image = next(i for i in proposed["items"] if i["kind"] == "image")
        self.assertEqual(image["risk"], "yellow")
        self.assertIn(templates.DOCKER_RISK_NOTE, image["description"])
