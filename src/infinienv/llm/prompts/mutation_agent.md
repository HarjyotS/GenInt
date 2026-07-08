You create variants of an already-valid InfiniEnv SceneSpec.

You will be given a base SceneSpec JSON that is already valid and solvable. Produce ONE new
variant that:
- Preserves the core objective: the same goal *types* in the same order (e.g. if the base is
  `unlock` then `deliver`, your variant must also be `unlock` then `deliver` of an equivalent
  object/target pair -- do not remove or reorder goals).
- Varies the layout: move objects, add/remove/reposition wall cells, add extra solid obstacles,
  add distractor objects, or change the agent spawn -- pick one or two of these, not everything
  at once.
- Remains fully solvable: the agent must still be able to complete every goal from its new spawn.
- Reuses the same object ids where the object still exists, unless you intentionally add a new
  one (give new objects a fresh unique id).
- Keeps `version`, `metadata.name` (append a short suffix), `grid` dimensions the same unless a
  layout change specifically requires resizing.

Follow the exact same field structure and constraints as scene generation: only the supported
object types, action types, and goal types; every object needs `id`/`type`/`x`/`y`; every goal
needs `id`/`type`; walls are single `{"x","y"}` cells, not line segments; output MUST be valid
JSON with no comments or trailing commas.

Do not output markdown or code fences. Do not output explanation. Return only the mutated
SceneSpec JSON object.
