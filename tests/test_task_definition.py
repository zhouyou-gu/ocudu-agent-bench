import unittest

from benchmark.benchmark_api.task_definition import agent_visible_task, load_all_tasks, load_task


class TaskDefinitionTests(unittest.TestCase):
    def test_current_tasks_load_in_private_contract_shape(self) -> None:
        tasks = load_all_tasks()

        self.assertIn("ws_prb_ping_v1", tasks)
        self.assertIn("ran_policy_triage_v1", tasks)
        for task in tasks.values():
            self.assertTrue(task.G)
            self.assertIsInstance(task.E, dict)
            self.assertIsInstance(task.U, dict)
            self.assertIsInstance(task.I, dict)
            self.assertIsInstance(task.J, dict)

    def test_agent_visible_task_redacts_private_fields(self) -> None:
        view = agent_visible_task(load_task("ws_prb_ping_v1")).to_dict()
        rendered = repr(view)

        self.assertEqual(view["task_id"], "ws_prb_ping_v1")
        self.assertIn("goal", view)
        self.assertNotIn("'E'", rendered)
        self.assertNotIn("'U'", rendered)
        self.assertNotIn("'J'", rendered)
        self.assertNotIn("oracle_requirements", rendered)
        self.assertNotIn("stimulus", rendered.lower())

    def test_no_action_is_not_a_task_allowed_runtime_action(self) -> None:
        tasks = load_all_tasks()
        for task in tasks.values():
            self.assertNotIn("NO_ACTION", task.allowed_actions)


if __name__ == "__main__":
    unittest.main()
