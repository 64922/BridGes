"""配方注册与校验：代码拒绝循环、未知能力与跳过前置。

配方由领域模块在导入/装配时登记；登记顺序必须与执行顺序一致，依赖
只能指向更早的节点。任何一步不合法都在注册时抛出，运行期不会以
“尽力而为”的方式猜测执行顺序。
"""

from __future__ import annotations

from collections.abc import Iterable

from bridges.kernel.contracts import RecipeDefinition


class RecipeValidationError(ValueError):
    """配方不合法：拒绝登记，不进入执行。"""

    def __init__(self, recipe_id: str, code: str, message: str) -> None:
        super().__init__(f"配方 {recipe_id} 非法（{code}）：{message}")
        self.recipe_id = recipe_id
        self.code = code
        self.message = message


class RecipeRegistry:
    """受控配方目录：能力集合、质量门集合与配方定义。"""

    def __init__(
        self,
        *,
        capabilities: Iterable[str],
        gates: Iterable[str],
    ) -> None:
        self._capabilities = frozenset(capabilities)
        self._gates = frozenset(gates)
        self._recipes: dict[str, RecipeDefinition] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def gates(self) -> frozenset[str]:
        return self._gates

    def register(self, recipe: RecipeDefinition) -> None:
        """登记配方；已存在的同 ID 配方以相同定义重放为幂等。"""
        self.validate(recipe)
        existing = self._recipes.get(recipe.recipe_id)
        if existing is not None and existing != recipe:
            raise RecipeValidationError(
                recipe.recipe_id,
                "recipe_conflict",
                "同一配方 ID 已登记不同定义；版本变更必须换新 ID 或显式迁移。",
            )
        self._recipes[recipe.recipe_id] = recipe

    def get(self, recipe_id: str) -> RecipeDefinition:
        recipe = self._recipes.get(recipe_id)
        if recipe is None:
            raise RecipeValidationError(recipe_id, "recipe_unknown", "配方未登记。")
        return recipe

    def validate(self, recipe: RecipeDefinition) -> None:
        """校验配方：必经顺序、依赖、能力与门；拒绝循环与跳过前置。"""
        if not recipe.recipe_id or not recipe.recipe_version:
            raise RecipeValidationError(
                recipe.recipe_id or "<empty>", "recipe_identity", "配方 ID 与版本不能为空。"
            )
        if not recipe.nodes:
            raise RecipeValidationError(
                recipe.recipe_id, "empty_recipe", "配方至少需要一个节点。"
            )
        names: dict[str, int] = {}
        for index, spec in enumerate(recipe.nodes):
            if spec.name in names:
                raise RecipeValidationError(
                    recipe.recipe_id, "duplicate_node", f"节点 {spec.name} 重复登记。"
                )
            names[spec.name] = index
            if spec.capability not in self._capabilities:
                raise RecipeValidationError(
                    recipe.recipe_id,
                    "unknown_capability",
                    f"节点 {spec.name} 引用了未登记能力 {spec.capability}。",
                )
            for gate in (*spec.required_gates, *spec.optional_gates):
                if gate not in self._gates:
                    raise RecipeValidationError(
                        recipe.recipe_id,
                        "unknown_gate",
                        f"节点 {spec.name} 引用了未登记质量门 {gate}。",
                    )
        for spec in recipe.nodes:
            for dependency in spec.depends_on:
                if dependency not in names:
                    raise RecipeValidationError(
                        recipe.recipe_id,
                        "unknown_prerequisite",
                        f"节点 {spec.name} 依赖未登记的前置 {dependency}。",
                    )
                if names[dependency] >= names[spec.name]:
                    raise RecipeValidationError(
                        recipe.recipe_id,
                        "skipped_prerequisite",
                        f"节点 {spec.name} 的前置 {dependency} 不在必经顺序之前（可能形成循环）。",
                    )
        self._assert_acyclic(recipe)

    def _assert_acyclic(self, recipe: RecipeDefinition) -> None:
        """显式无环校验：即使顺序约束被放宽也拒绝循环。"""
        index = {spec.name: i for i, spec in enumerate(recipe.nodes)}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise RecipeValidationError(
                    recipe.recipe_id, "cyclic_recipe", f"节点 {name} 形成循环依赖。"
                )
            visiting.add(name)
            for dependency in recipe.node(name).depends_on:
                if dependency in index:
                    visit(dependency)
            visiting.discard(name)
            visited.add(name)

        for spec in recipe.nodes:
            visit(spec.name)
