"""Tests for the TOML league-config loader.

Uses docs/league-config.toml.example as the base fixture — it is committed,
loadable, and deliberately contains 36 dummy keepers (3 per team) plus a
same-round collision for Team Name 3, and two traded picks.

Every validation path is asserted to name the offending field. A config error
that does not say *which* row or key is wrong is nearly useless when it fires
nine days before a draft.
"""

import re
from pathlib import Path

import pytest

from engine.schedule import build_pick_schedule, live_picks, team_live_picks
from sources.league_config import ConfigError, load_league_config

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "league-config.toml.example"


@pytest.fixture
def example_text() -> str:
    return EXAMPLE.read_text()


def write_cfg(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "league-config.toml"
    p.write_text(text)
    return p


def load_mutated(tmp_path: Path, text: str, old: str, new: str):
    """Replace `old` with `new` (asserting it matched) and load the result."""
    assert old in text, f"fixture drift: pattern not found -> {old!r}"
    return load_league_config(write_cfg(tmp_path, text.replace(old, new, 1)))


# --- happy path ------------------------------------------------------------

def test_example_config_loads(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    assert len(cfg.teams) == 12
    assert cfg.num_rounds == 22
    assert len(cfg.keepers) == 36
    assert len(cfg.trades) == 2
    assert cfg.draft_order == [t.team_id for t in cfg.teams]


def test_teams_are_ordered_by_draft_slot(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    assert [t.slot for t in cfg.teams] == list(range(1, 13))


def test_my_team_resolves_to_a_team_id(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    assert cfg.my_team_id in {t.team_id for t in cfg.teams}


def test_example_builds_a_legal_schedule(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    sched = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)
    assert len(sched) == 264
    assert len(live_picks(sched)) == 228
    assert all(len(team_live_picks(sched, t.team_id)) == 19 for t in cfg.teams)


# --- collision cascade -----------------------------------------------------

def test_example_contains_a_same_round_collision(example_text, tmp_path):
    """Team Name 3 keeps two players declared in round 7 — the fixture's point."""
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    t3 = next(t for t in cfg.teams if t.name == "Team Name 3")
    declared = sorted(k.declared_round for k in cfg.keepers if k.team_id == t3.team_id)
    assert declared.count(7) == 2, "fixture should keep two players in round 7"


def test_collision_cascades_to_the_earlier_round(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    sched = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)
    t3 = next(t for t in cfg.teams if t.name == "Team Name 3")
    kept = {p.round for p in sched if p.kind == "keeper" and p.team_id == t3.team_id}
    # declared 7, 7, 16 -> occupies 7, 6, 16 (always shifts EARLIER)
    assert kept == {6, 7, 16}
    cascaded = [
        p for p in sched
        if p.kind == "keeper" and p.team_id == t3.team_id and p.round != p.declared_round
    ]
    assert len(cascaded) == 1
    assert cascaded[0].declared_round == 7 and cascaded[0].round == 6


def test_collision_does_not_change_live_pick_totals(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    sched = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)
    t3 = next(t for t in cfg.teams if t.name == "Team Name 3")
    assert len(team_live_picks(sched, t3.team_id)) == 19


# --- traded picks ----------------------------------------------------------

def test_trades_resolve_names_to_ids(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    ids = {t.name: t.team_id for t in cfg.teams}
    assert {(t.from_team_id, t.to_team_id, t.round) for t in cfg.trades} == {
        (ids["Team Name 1"], ids["Team Name 6"], 19),
        (ids["Team Name 6"], ids["Team Name 1"], 22),
    }


def test_traded_pick_changes_owner_not_position(example_text, tmp_path):
    cfg = load_league_config(write_cfg(tmp_path, example_text))
    sched = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)
    ids = {t.name: t.team_id for t in cfg.teams}
    traded = [p for p in sched if p.is_traded]
    assert len(traded) == 2
    for p in traded:
        assert p.team_id != p.original_team_id
    r19 = next(p for p in traded if p.round == 19)
    assert r19.original_team_id == ids["Team Name 1"]
    assert r19.team_id == ids["Team Name 6"]


def test_empty_trades_list_is_legal(example_text, tmp_path):
    # anchored to line start: a comment above the array also contains this text
    cfg = load_mutated(
        tmp_path, example_text,
        '\ntraded_picks = [', '\ntraded_picks = []\n_unused_rows = [',
    )
    assert cfg.trades == []


def test_keeping_a_traded_round_is_rejected_by_the_engine(example_text, tmp_path):
    """Team Name 1 trades R19; make it also keep R19. Engine must refuse."""
    cfg = load_mutated(
        tmp_path, example_text,
        '{ team = "Team Name 1", player = "Aaron Vasquez", round =  2 }',
        '{ team = "Team Name 1", player = "Aaron Vasquez", round = 19 }',
    )
    with pytest.raises(ValueError, match="keeper"):
        build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)


# --- file-level failures ---------------------------------------------------

def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError, match="league config not found"):
        load_league_config(tmp_path / "nope.toml")


def test_malformed_toml_names_the_file_and_line(example_text, tmp_path):
    with pytest.raises(ConfigError, match=r"invalid TOML"):
        load_mutated(tmp_path, example_text, "season = 2026", "season: 2026")


def test_error_messages_are_prefixed_with_the_filename(example_text, tmp_path):
    with pytest.raises(ConfigError, match=r"^league-config\.toml:"):
        load_mutated(tmp_path, example_text, "league_id = 12345", "")


# --- missing keys name the dotted path -------------------------------------

@pytest.mark.parametrize(
    "old,new,missing",
    [
        ("league_id = 12345", "", "basics.league_id"),
        ("season = 2026", "", "basics.season"),
        ("draft_rounds", "draft_roundz", "basics.draft_rounds"),
        ("my_team = ", "my_teamz = ", "basics.my_team"),
    ],
)
def test_missing_basics_key_names_the_dotted_path(example_text, tmp_path, old, new, missing):
    with pytest.raises(ConfigError) as e:
        load_mutated(tmp_path, example_text, old, new)
    assert missing in str(e.value)


def test_missing_section_names_the_path_and_lists_siblings(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(tmp_path, example_text, "[basics]", "[basicz]")
    msg = str(e.value)
    assert "missing required key 'basics'" in msg
    assert "keys present" in msg and "basicz" in msg


def test_missing_draft_order_names_the_path(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(tmp_path, example_text, "order = [", "orderz = [")
    assert "draft.order" in str(e.value)


def test_missing_traded_picks_key_names_the_path(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(tmp_path, example_text, "\ntraded_picks = [", "\ntrades = [")
    assert "draft.traded_picks" in str(e.value)


# --- malformed rows name the row index -------------------------------------

def test_draft_order_row_missing_team_id_names_the_row(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(
            tmp_path, example_text,
            '{ slot =  3, name = "Team Name 3",  team_id =  3 }',
            '{ slot =  3, name = "Team Name 3" }',
        )
    msg = str(e.value)
    assert "draft.order[2]" in msg  # 0-based index of the third row
    assert "team_id" in msg


def test_keeper_row_missing_player_names_the_row(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(
            tmp_path, example_text,
            '{ team = "Team Name 1", player = "Aaron Vasquez", round =  2 }',
            '{ team = "Team Name 1", round =  2 }',
        )
    msg = str(e.value)
    assert "draft.keepers[0]" in msg
    assert "player" in msg


def test_trade_row_missing_round_names_the_row(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(
            tmp_path, example_text,
            '{ from_team = "Team Name 1", to_team = "Team Name 6", round = 19 }',
            '{ from_team = "Team Name 1", to_team = "Team Name 6" }',
        )
    msg = str(e.value)
    assert "draft.traded_picks[0]" in msg
    assert "round" in msg


# --- unknown team names ----------------------------------------------------

def test_unknown_keeper_team_names_row_and_lists_valid_teams(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(
            tmp_path, example_text,
            '{ team = "Team Name 1", player = "Aaron Vasquez", round =  2 }',
            '{ team = "Team Nmae 1", player = "Aaron Vasquez", round =  2 }',
        )
    msg = str(e.value)
    assert "draft.keepers[0]" in msg
    assert "Team Nmae 1" in msg
    assert "Team Name 1" in msg  # lists the valid options


def test_unknown_trade_team_names_the_row(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(
            tmp_path, example_text,
            'from_team = "Team Name 1", to_team = "Team Name 6", round = 19',
            'from_team = "Ghost Team", to_team = "Team Name 6", round = 19',
        )
    msg = str(e.value)
    assert "draft.traded_picks[0]" in msg and "Ghost Team" in msg


def test_unknown_my_team_names_the_field(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(tmp_path, example_text, 'my_team = "Team Name 6"', 'my_team = "Nobody"')
    msg = str(e.value)
    assert "basics.my_team" in msg and "Nobody" in msg


# --- keeper count validation -----------------------------------------------

def test_empty_keeper_list_is_a_hard_error(example_text, tmp_path):
    text = re.sub(r"keepers = \[.*?\n\]", "keepers = []", example_text, flags=re.S)
    assert "keepers = []" in text
    with pytest.raises(ConfigError, match="draft.keepers is empty"):
        load_league_config(write_cfg(tmp_path, text))


def test_wrong_keeper_count_names_the_offending_team(example_text, tmp_path):
    with pytest.raises(ConfigError) as e:
        load_mutated(
            tmp_path, example_text,
            '{ team = "Team Name 1", player = "Aaron Vasquez", round =  2 },', "",
        )
    msg = str(e.value)
    assert "draft.keepers" in msg
    assert "35" in msg and "36" in msg
    assert "Team Name 1" in msg  # names the team, not a bare id


def test_team_with_no_keepers_at_all_is_named(example_text, tmp_path):
    text = example_text
    for name in ("Aaron Vasquez", "Ben Okafor", "Carter Lindqvist"):
        text = re.sub(rf'\s*\{{ team = "Team Name 1", player = "{name}"[^}}]*\}},', "", text)
    with pytest.raises(ConfigError) as e:
        load_league_config(write_cfg(tmp_path, text))
    assert "Team Name 1" in str(e.value)
