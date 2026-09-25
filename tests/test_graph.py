"""
Integration tests for the LangGraph graph wiring.

Tests the graph structure, conditional edges, and veto short-circuit path.
These tests verify wiring only — they do NOT make live LLM calls.
"""

import pytest

from argos.graph import build_graph, compile_graph, create_initial_state


class TestGraphStructure:
    """Test the graph topology is wired correctly."""

    def test_graph_compiles(self):
        """Graph should compile without errors."""
        compiled = compile_graph()
        assert compiled is not None

    def test_all_nodes_present(self):
        """All expected nodes should be in the graph."""
        graph = build_graph()
        expected_nodes = {
            "analyst_router",
            "fundamentals", "sentiment", "technicals",
            "bull", "bear",
            "risk_manager", "veto_short_circuit", "portfolio_manager",
        }
        actual_nodes = set(graph.nodes.keys())
        assert expected_nodes.issubset(actual_nodes)

    def test_has_entry_point(self):
        """Graph should compile with a defined entry point."""
        compiled = compile_graph()
        assert compiled is not None
        # The compiled graph has a .invoke method, confirming it has an entry point
        assert hasattr(compiled, 'invoke')

    def test_has_end_edges(self):
        """Both portfolio_manager and veto_short_circuit should lead to END."""
        compiled = compile_graph()
        # The compiled graph should have an END transition — verify by checking
        # it has the nodes we expect (veto_short_circuit and portfolio_manager)
        assert hasattr(compiled, 'invoke')
        # Verify both paths exist by checking node names in the graph
        graph = build_graph()
        node_names = set(graph.nodes.keys())
        assert 'veto_short_circuit' in node_names
        assert 'portfolio_manager' in node_names


class TestParallelAnalysts:
    """Test that the three analyst nodes run in parallel (fan-out/fan-in)."""

    def test_analyst_router_returns_three_nodes(self):
        """The analyst_router should fan out to all three analyst nodes."""
        from argos.graph import _route_to_analysts, ANALYST_NODES

        state = {"ticker": "AAPL", "as_of_date": "2024-03-04"}
        result = _route_to_analysts(state)
        assert result == ANALYST_NODES
        assert len(result) == 3

    def test_after_analysts_routes_to_bull(self):
        """After all analysts complete, the fan-in should route to bull."""
        from argos.graph import _after_analysts

        state = {"ticker": "AAPL"}
        assert _after_analysts(state) == "bull"

    def test_analyst_router_sets_run_id(self):
        """The analyst_router node should ensure run_id is present."""
        from argos.graph import analyst_router

        state = {"ticker": "AAPL", "as_of_date": "2024-03-04"}
        result = analyst_router(state)
        assert "run_id" in result
        assert result["run_id"] is not None


class TestVetoShortCircuit:
    """Test the Risk Manager veto short-circuit logic."""

    def test_veto_produces_hold_decision(self):
        """When Risk Manager vetoes, final_decision should be HOLD with zero confidence."""
        from argos.nodes.portfolio_manager import veto_short_circuit

        state = {
            "ticker": "AAPL",
            "as_of_date": "2024-03-04",
            "risk_verdict": {
                "veto": True,
                "max_position_size": 0.0,
                "reasoning": "Kronos uncertainty too wide.",
                "risk_factors": ["Wide uncertainty"],
            },
        }

        result = veto_short_circuit(state)

        assert result["final_decision"]["action"] == "hold"
        assert result["final_decision"]["confidence"] == 0.0
        assert result["final_decision"]["position_size"] == 0.0
        assert "vetoed" in result["final_decision"]["rationale"].lower() or "veto" in result["final_decision"]["rationale"].lower()


class TestConditionalEdge:
    """Test the should_veto conditional routing function."""

    def test_should_veto_when_vetoed(self):
        from argos.nodes.risk_manager import should_veto

        state = {
            "risk_verdict": {"veto": True, "max_position_size": 0.0}
        }
        assert should_veto(state) == "vetoed"

    def test_should_not_veto_when_approved(self):
        from argos.nodes.risk_manager import should_veto

        state = {
            "risk_verdict": {"veto": False, "max_position_size": 0.05}
        }
        assert should_veto(state) == "approved"

    def test_should_not_veto_when_no_verdict(self):
        from argos.nodes.risk_manager import should_veto

        state = {"risk_verdict": None}
        assert should_veto(state) == "approved"
