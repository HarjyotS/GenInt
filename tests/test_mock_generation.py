from infinienv.llm.providers.mock import MockProvider
from infinienv.validation.validator import validate_scene


def test_mock_provider_creates_deterministic_valid_scene():
    provider = MockProvider()
    scene_a = provider.generate_scene("Create a kitchen delivery task", seed=42)
    scene_b = provider.generate_scene("Create a kitchen delivery task", seed=42)
    assert scene_a.model_dump() == scene_b.model_dump()

    result = validate_scene(scene_a)
    assert result.valid, result.errors


def test_mock_provider_routes_by_keyword():
    provider = MockProvider()
    kitchen = provider.generate_scene("bring the can to the sink", seed=1)
    warehouse = provider.generate_scene("find the key and unlock the door", seed=1)
    obstacle = provider.generate_scene("navigate the maze avoiding hazards", seed=1)
    assert kitchen.metadata.theme == "kitchen"
    assert warehouse.metadata.theme == "warehouse"
    assert obstacle.metadata.theme == "obstacle_course"


def test_push_template_is_deterministic_and_valid():
    from infinienv.generation.templates import generate_from_template, pick_template_name
    from infinienv.validation.validator import validate_scene

    assert pick_template_name("push the crate onto the switch") == "push"
    assert pick_template_name("slide a puck across the ice") == "push"

    s1 = generate_from_template("push a block onto the target", 5)
    s2 = generate_from_template("push a block onto the target", 5)
    assert s1.model_dump() == s2.model_dump()

    result = validate_scene(s1)
    assert result.valid, [e.code for e in result.errors]
    assert any(g.type == "push" for g in s1.goals)
