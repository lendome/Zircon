from zirconAgent.core.stealth_prompts import (
    StealthActivator,
    get_stealth_prompts,
    inject_stealth_prompts,
)


class TestBoldExecutionActivation:
    def test_nothing_is_off_limits_fires(self):
        prompts = get_stealth_prompts("Rewrite this module. Nothing is off limits.")
        assert any("BOLD EXECUTION MODE" in p for p in prompts)

    def test_do_whatever_it_takes_fires(self):
        prompts = get_stealth_prompts("The tests are red — do whatever it takes to fix them")
        assert any("BOLD EXECUTION MODE" in p for p in prompts)

    def test_radical_fires(self):
        prompts = get_stealth_prompts("Make a radical change to the architecture")
        assert any("BOLD EXECUTION MODE" in p for p in prompts)

    def test_move_fast_and_break_fires(self):
        prompts = get_stealth_prompts("Move fast and break things on this one")
        assert any("BOLD EXECUTION MODE" in p for p in prompts)

    def test_activation_fires_once_per_task(self):
        # Multiple trigger phrases in one task must not duplicate the block.
        prompts = get_stealth_prompts("Nothing is off limits — do whatever it takes, be aggressive")
        bold = [p for p in prompts if "BOLD EXECUTION MODE" in p]
        assert len(bold) == 1

    def test_prompt_suspends_minimal_edit_rules(self):
        prompts = get_stealth_prompts("nothing is off limits")
        bold = next(p for p in prompts if "BOLD EXECUTION MODE" in p)
        assert "SUSPENDED" in bold
        assert "algorithm" in bold
        assert "cache" in bold  # the canonical cheap way out is named

    def test_neutral_task_does_not_fire(self):
        prompts = get_stealth_prompts("Fix the off-by-one error in the parser limits handling")
        assert not any("BOLD EXECUTION MODE" in p for p in prompts)

    def test_substring_false_positives_do_not_fire(self):
        # Word-boundary matching: "reversible" contains no trigger; "limitless"
        # is not "limits". "aggressive" alone is not "be aggressive".
        prompts = get_stealth_prompts("This reversible transform has limitless potential; describe it aggressively")
        assert not any("BOLD EXECUTION MODE" in p for p in prompts)


class TestActivatorMechanics:
    def test_inject_appends_before_platform_block(self):
        base = "RULES\n\n## PLATFORM\nwindows stuff"
        out = inject_stealth_prompts(base, "nothing is off limits")
        assert "BOLD EXECUTION MODE" in out
        assert out.index("BOLD EXECUTION MODE") < out.index("## PLATFORM")

    def test_inject_no_match_returns_base(self):
        base = "RULES"
        assert inject_stealth_prompts(base, "typo in the readme") == base

    def test_custom_registration(self):
        act = StealthActivator()
        act.register(keywords=[["turbo mode"]], prompt="TURBO")
        assert act.get_prompts("enable turbo mode now") == ["TURBO"]
