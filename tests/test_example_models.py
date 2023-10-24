import shutil

import calliope
import numpy as np
import pandas as pd
import pytest
from calliope import exceptions
from pytest import approx

from .common.util import check_error_or_warning


class TestModelPreproccessing:
    def test_preprocess_national_scale(self):
        calliope.examples.national_scale()

    @pytest.mark.time_intensive
    def test_preprocess_time_clustering(self):
        calliope.examples.time_clustering()

    def test_preprocess_time_resampling(self):
        calliope.examples.time_resampling()

    def test_preprocess_urban_scale(self):
        calliope.examples.urban_scale()

    @pytest.mark.filterwarnings("ignore:(?s).*Integer:calliope.exceptions.ModelWarning")
    def test_preprocess_milp(self):
        calliope.examples.milp()

    @pytest.mark.xfail(reason="Not expecting operate mode to work at the moment")
    def test_preprocess_operate(self):
        calliope.examples.operate()

    def test_preprocess_time_masking(self):
        calliope.examples.time_masking()


class TestNationalScaleExampleModelSenseChecks:
    def example_tester(self, solver="cbc", solver_io=None):
        model = calliope.examples.national_scale(
            override_dict={"model.subset_time": ["2005-01-01", "2005-01-01"]}
        )

        model.run_config["solver"] = solver
        model.run_config["solver_io"] = solver_io

        model.build()
        model.solve()

        assert model.results.storage_cap.sel(nodes="region1-1", techs="csp") == approx(
            45129.950
        )
        assert model.results.storage_cap.sel(
            nodes="region2", techs="battery"
        ) == approx(6675.173)

        assert model.results.flow_cap.sel(nodes="region1-1", techs="csp") == approx(
            4626.588
        )
        assert model.results.flow_cap.sel(nodes="region2", techs="battery") == approx(
            1000
        )
        assert model.results.flow_cap.sel(nodes="region1", techs="ccgt") == approx(
            30000
        )

        assert float(model.results.cost.sum()) == approx(38988.7442)

        assert float(
            model.results.systemwide_levelised_cost.sel(
                carriers="power", techs="battery"
            ).item()
        ) == approx(0.063543, abs=0.000001)
        assert float(
            model.results.systemwide_capacity_factor.sel(
                carriers="power", techs="battery"
            ).item()
        ) == approx(0.2642256, abs=0.000001)

    def test_nationalscale_example_results_cbc(self):
        self.example_tester()

    def test_nationalscale_example_results_gurobi(self):
        pytest.importorskip("gurobipy")
        self.example_tester(solver="gurobi", solver_io="python")

    def test_nationalscale_example_results_cplex(self):
        if shutil.which("cplex"):
            self.example_tester(solver="cplex")
        else:
            pytest.skip("CPLEX not installed")

    def test_nationalscale_example_results_glpk(self):
        if shutil.which("glpsol"):
            self.example_tester(solver="glpk")
        else:
            pytest.skip("GLPK not installed")

    def test_considers_supply_generation_only_in_total_levelised_cost(self):
        # calculation of expected value:
        # costs = model.inputs.cost.sum(dim="locs")
        # gen = model.inputs.flow_out.sum(dim=["timesteps", "locs"])
        # lcoe = costs.sum(dim="techs") / gen.sel(techs=["ccgt", "csp"]).sum(dim="techs")
        model = calliope.examples.national_scale()
        model.build()
        model.solve()

        assert model.results.total_levelised_cost.item() == approx(0.067005, abs=1e-5)

    def test_fails_gracefully_without_timeseries(self):
        override = {
            "nodes.region1.techs.demand_power.constraints.sink_equals": 200,
            "nodes.region2.techs.demand_power.constraints.sink_equals": 400,
            "techs.csp.constraints.source_max": 100,
        }
        with pytest.raises(calliope.exceptions.ModelError):
            calliope.examples.national_scale(override_dict=override)


@pytest.mark.xfail(reason="Not reimplemented the 'check feasibility' objective")
class TestNationalScaleExampleModelInfeasibility:
    def example_tester(self):
        model = calliope.examples.national_scale(
            scenario="check_feasibility", override_dict={"run.cyclic_storage": False}
        )

        model.build()
        model.solve()

        assert model.results.termination_condition in [
            "infeasible",
            "other",
        ]  # glpk gives 'other' as result

        assert len(model.results.data_vars) == 0
        assert "flow_cap" not in model._model_data.data_vars

    def test_nationalscale_example_results_cbc(self):
        self.example_tester()


@pytest.mark.xfail(reason="Not expecting operate mode to work at the moment")
class TestNationalScaleExampleModelOperate:
    def example_tester(self):
        with pytest.warns(calliope.exceptions.ModelWarning) as excinfo:
            model = calliope.examples.national_scale(
                override_dict={"model.subset_time": ["2005-01-01", "2005-01-03"]},
                scenario="operate",
            )
            model.build()

        expected_warning = "Source capacity constraint defined and set to infinity for all supply_plus techs"

        assert check_error_or_warning(excinfo, expected_warning)

        model.solve()
        assert all(
            model.results.timesteps
            == pd.date_range("2005-01", "2005-01-03 23:00:00", freq="H")
        )

    def test_nationalscale_example_results_cbc(self):
        self.example_tester()


@pytest.mark.skip(
    reason="SPORES mode will fail until the cost max group constraint can be reproduced"
)
class TestNationalScaleExampleModelSpores:
    def example_tester(self, solver="cbc", solver_io=None):
        model = calliope.examples.national_scale(
            override_dict={
                "model.subset_time": ["2005-01-01", "2005-01-03"],
                "run.solver": solver,
                "run.solver_io": solver_io,
            },
            scenario="spores",
        )

        model.build()

        # The initial state of the objective cost class scores should be monetary: 1, spores_score: 0
        assert (
            model.backend.parameters.objective_cost_class["monetary"].item().value == 1
        )
        assert (
            model.backend.parameters.objective_cost_class["spores_score"].item().value
            == 0
        )

        model.solve()
        # Expecting three spores + first optimal run
        assert np.allclose(model.results.spores, [0, 1, 2, 3])

        costs = model.results.cost.sum(["nodes", "techs"])
        slack_cost = model.backend.parameters.cost_max.item().value

        # First run is the optimal run, everything else is coming up against the slack cost
        assert costs.loc[{"spores": 0, "costs": "monetary"}] * (
            1 + model.run_config["spores_options"]["slack"]
        ) == approx(slack_cost)
        assert all(
            costs.loc[{"spores": slice(1, None), "costs": "monetary"}]
            <= slack_cost * 1.0001
        )

        # In each iteration, the spores_score has to increase
        assert all(costs.diff("spores").loc[{"costs": "spores_score"}] >= 0)

        # The final state of the objective cost class scores should be monetary: 0, spores_score: 1
        assert (
            model.backend.parameters.objective_cost_class["monetary"].item().value == 0
        )
        assert (
            model.backend.parameters.objective_cost_class["spores_score"].item().value
            == 1
        )
        return model._model_data

    def test_nationalscale_example_results_cbc(self):
        self.example_tester()

    @pytest.mark.filterwarnings(
        "ignore:(?s).*`gurobi_persistent`.*:calliope.exceptions.ModelWarning"
    )
    @pytest.mark.filterwarnings(
        "ignore:(?s).*Updating the Pyomo parameter.*:calliope.exceptions.ModelWarning"
    )
    def test_nationalscale_example_results_gurobi(self):
        pytest.importorskip("gurobipy")
        gurobi_data = self.example_tester(solver="gurobi", solver_io="python")
        gurobi_persistent_data = self.example_tester(
            solver="gurobi_persistent", solver_io="python"
        )
        assert np.allclose(gurobi_data.flow_cap, gurobi_persistent_data.flow_cap)
        assert np.allclose(gurobi_data.cost, gurobi_persistent_data.cost)

    @pytest.fixture
    def base_model_data(self):
        model = calliope.examples.national_scale(
            override_dict={
                "model.subset_time": ["2005-01-01", "2005-01-03"],
                "run.solver": "cbc",
            },
            scenario="spores",
        )

        model.build()
        model.solve()

        return model._model_data

    @pytest.mark.parametrize("init_spore", (0, 1, 2))
    def test_nationalscale_skip_cost_op_spores(self, base_model_data, init_spore):
        spores_model = calliope.Model(
            config=None, model_data=base_model_data.loc[{"spores": [init_spore + 1]}]
        )
        spores_model._model_data.coords["spores"] = [init_spore]

        spores_model.run_config["spores_options"]["skip_cost_op"] = True

        spores_model.build()
        spores_model.solve(force=True)

        assert set(spores_model.results.spores.values) == set(range(init_spore, 4))
        assert base_model_data.loc[{"spores": slice(init_spore + 1, None)}].equals(
            spores_model._model_data.loc[{"spores": slice(init_spore + 1, None)}]
        )

    def test_fail_with_spores_as_input_dim(self, base_model_data):
        spores_model = calliope.Model(
            config=None, model_data=base_model_data.loc[{"spores": [0, 1]}]
        )
        with pytest.raises(exceptions.ModelError) as excinfo:
            spores_model.build()
            spores_model.solve(force=True)
        assert check_error_or_warning(
            excinfo, "Cannot run SPORES with a SPORES dimension in any input"
        )

    @pytest.fixture
    def spores_with_override(self):
        def _spores_with_override(override_dict):
            result_without_override = self.example_tester()
            result_with_override = self.example_tester(**override_dict)
            assert result_without_override.flow_cap.round(5).equals(
                result_with_override.flow_cap.round(5)
            )
            assert (
                result_without_override.cost.sel(costs="spores_score")
                .round(5)
                .to_series()
                .drop("region1::ccgt", level="loc_techs_cost")
                .equals(
                    result_with_override.cost.sel(costs="spores_score")
                    .round(5)
                    .to_series()
                    .drop("region1::ccgt", level="loc_techs_cost")
                )
            )
            assert (
                result_without_override.cost.sel(
                    costs="spores_score", loc_techs_cost="region1::ccgt"
                ).sum()
                > 0
            )
            return result_with_override, result_without_override

        return _spores_with_override

    @pytest.mark.parametrize("override", ("flow_cap_min", "flow_cap_equals"))
    def test_ignore_forced_flow_cap_spores(self, spores_with_override, override):
        # the national scale model always maxes out CCGT in the first 3 SPORES.
        # So we can force its minimum/exact capacity without influencing other tech SPORE scores.
        # This enables us to test our functionality that only *additional* capacity is scored.
        override_dict = {f"locations.region1.techs.ccgt.constraints.{override}": 30000}
        result_with_override, _ = spores_with_override(override_dict)
        assert (
            result_with_override.cost.sel(
                costs="spores_score", loc_techs_cost="region1::ccgt"
            ).sum()
            == 0
        )

    def test_ignore_forced_flow_cap_spores_some_ccgt_score(self, spores_with_override):
        # the national scale model always maxes out CCGT in the first 3 SPORES.
        # So we can force its minimum/exact capacity without influencing other tech SPORE scores.
        # This enables us to test our functionality that only *additional* capacity is scored.
        override_dict = {"locations.region1.techs.ccgt.constraints.flow_cap_min": 15000}
        result_with_override, _ = spores_with_override(override_dict)
        assert (
            result_with_override.cost.sel(
                costs="spores_score", loc_techs_cost="region1::ccgt"
            ).sum()
            > 0
        )

    def test_ignore_forced_flow_cap_spores_no_double_counting(
        self, spores_with_override
    ):
        # the national scale model always maxes out CCGT in the first 3 SPORES.
        # So we can force its minimum/exact capacity without influencing other tech SPORE scores.
        # This enables us to test our functionality that only *additional* capacity is scored.
        override_dict = {
            "locations.region1.techs.ccgt.constraints.flow_cap_min": 15000,
            "locations.region1.techs.ccgt.constraints.flow_cap_equals": 30000,
        }
        result_with_override, _ = spores_with_override(override_dict)
        assert (
            result_with_override.cost.sel(
                costs="spores_score", loc_techs_cost="region1::ccgt"
            ).sum()
            == 0
        )


class TestNationalScaleResampledExampleModelSenseChecks:
    def example_tester(self, solver="cbc", solver_io=None):
        override = {
            "model.subset_time": ["2005-01-01", "2005-01-01"],
            "run.solver": solver,
        }

        if solver_io:
            override["run.solver_io"] = solver_io

        model = calliope.examples.time_resampling(override_dict=override)
        model.build()
        model.solve()

        assert model.results.storage_cap.sel(nodes="region1-1", techs="csp") == approx(
            23563.444
        )
        assert model.results.storage_cap.sel(
            nodes="region2", techs="battery"
        ) == approx(6315.78947)

        assert model.results.flow_cap.sel(nodes="region1-1", techs="csp") == approx(
            1440.8377
        )
        assert model.results.flow_cap.sel(nodes="region2", techs="battery") == approx(
            1000
        )
        assert model.results.flow_cap.sel(nodes="region1", techs="ccgt") == approx(
            30000
        )

        assert float(model.results.cost.sum()) == approx(37344.221869)

        assert float(
            model.results.systemwide_levelised_cost.loc[
                {"carriers": "power", "techs": "battery"}
            ].item()
        ) == approx(0.063543, abs=0.000001)
        assert float(
            model.results.systemwide_capacity_factor.loc[
                {"carriers": "power", "techs": "battery"}
            ].item()
        ) == approx(0.25, abs=0.000001)

    def test_nationalscale_example_results_cbc(self):
        self.example_tester()

    def test_nationalscale_resampled_example_results_glpk(self):
        if shutil.which("glpsol"):
            self.example_tester(solver="glpk")
        else:
            pytest.skip("GLPK not installed")


@pytest.mark.time_intensive
class TestNationalScaleClusteredExampleModelSenseChecks:
    def model_runner(
        self,
        expected_total_cost=None,
        expected_levelised_cost=None,
        expected_capacity_factor=None,
        solver="cbc",
        solver_io=None,
        how="closest",
        storage_inter_cluster=False,
        cyclic=False,
        storage=True,
        backend_runner="run",
    ):
        override = {
            "model.time.function_options": {
                "how": how,
                "storage_inter_cluster": storage_inter_cluster,
            },
            "run.solver": solver,
            "run.cyclic_storage": cyclic,
        }
        if storage is False:
            override.update({"techs.battery.exists": False, "techs.csp.exists": False})
        if solver_io is not None:
            override["run.solver_io"] = solver_io
        if storage_inter_cluster and backend_runner == "solve":
            override["model.custom_math"] = ["storage_inter_cluster"]

        model = calliope.examples.time_clustering(override_dict=override)
        timesteps = model._model_data.timesteps.copy(deep=True)

        model.build()
        model.solve()

        # make sure the dimension items have not been accidentally reordered
        assert timesteps.equals(model._model_data.timesteps)

        if expected_total_cost is not None:
            # Full 1-hourly model run: 22389323.5 with cyclic storage, 22389455.6 without
            assert float(model.results.cost.sum()) == approx(expected_total_cost)

        if expected_levelised_cost is not None:
            # Full 1-hourly model run: 0.316745 with cyclic storage, 0.316745 without
            assert float(
                model.results.systemwide_levelised_cost.loc[
                    {"carriers": "power", "techs": "battery"}
                ].item()
            ) == approx(expected_levelised_cost, abs=0.000001)

        if expected_capacity_factor is not None:
            # Full 1-hourly model run: 0.067998 with cycling storage, 0.067998 without
            assert float(
                model.results.systemwide_capacity_factor.loc[
                    {"carriers": "power", "techs": "battery"}
                ].item()
            ) == approx(expected_capacity_factor, abs=0.000001)

        return None

    def example_tester_closest(self, backend_runner, solver="cbc", solver_io=None):
        self.model_runner(
            solver=solver,
            expected_total_cost=51711873.177,  # was 49670627.15297682 when clustering with sklearn < v0.24
            expected_levelised_cost=0.111456,  # was 0.137105 when clustering with sklearn < v0.24
            expected_capacity_factor=0.074809,  # was 0.064501 when clustering with sklearn < v0.24
            solver_io=solver_io,
            how="closest",
            backend_runner=backend_runner,
        )

    def example_tester_mean(self, backend_runner, solver="cbc", solver_io=None):
        self.model_runner(
            solver=solver,
            expected_total_cost=45110416.434,  # was 22172253.328 when clustering with sklearn < v0.24
            expected_levelised_cost=0.126098,  # was 0.127783 when clustering with sklearn < v0.24
            expected_capacity_factor=0.047596,  # was 0.044458 when clustering with sklearn < v0.24
            solver_io=solver_io,
            how="mean",
            backend_runner=backend_runner,
        )

    @pytest.mark.parametrize("backend_runner", ["run", "solve"])
    def test_nationalscale_clustered_example_closest_results_cbc(self, backend_runner):
        self.example_tester_closest(backend_runner)

    @pytest.mark.parametrize("backend_runner", ["run", "solve"])
    def test_nationalscale_clustered_example_mean_results_cbc(self, backend_runner):
        self.example_tester_mean(backend_runner)

    def test_nationalscale_clustered_example_storage_inter_cluster(self):
        self.model_runner(
            expected_total_cost=33353390.626,  # was 21825515.304 when clustering with sklearn < v0.24
            expected_levelised_cost=0.115866,  # was 0.100760 when clustering with sklearn < v0.24
            expected_capacity_factor=0.074167,  # was 0.091036 when clustering with sklearn < v0.24
            storage_inter_cluster=True,
            backend_runner="solve",
        )

    def test_storage_inter_cluster_cyclic(self):
        self.model_runner(
            expected_total_cost=18838244.197,  # was 18904055.722 when clustering with sklearn < v0.24
            expected_levelised_cost=0.133110,  # was 0.122564 when clustering with sklearn < v0.24
            expected_capacity_factor=0.071411,  # was 0.075145 when clustering with sklearn < v0.24
            storage_inter_cluster=True,
            cyclic=True,
            backend_runner="solve",
        )


class TestUrbanScaleExampleModelSenseChecks:
    def example_tester(self, source_unit, solver="cbc", solver_io=None):
        unit_override = {
            "techs.pv.constraints.source_equals": "file=pv_resource.csv:{}".format(
                source_unit
            ),
            "techs.pv.switches.source_unit": source_unit,
            "run.solver": solver,
        }
        override = {"model.subset_time": ["2005-07-01", "2005-07-01"], **unit_override}

        if solver_io:
            override["run.solver_io"] = solver_io

        model = calliope.examples.urban_scale(override_dict=override)

        model.build()
        model.solve()

        assert model.results.flow_cap.sel(nodes="X1", techs="chp") == approx(250.090112)

        # GLPK isn't able to get the same answer both times, so we have to account for that here
        if source_unit == "per_cap" and solver == "glpk":
            heat_pipe_approx = 183.45825
        else:
            heat_pipe_approx = 182.19260

        assert model.results.flow_cap.sel(nodes="X2", techs="heat_pipes:N1") == approx(
            heat_pipe_approx
        )

        assert model.results.flow_out.sum("timesteps").sel(
            carriers="heat", nodes="X3", techs="boiler"
        ) == approx(0.18720)
        assert model.results.area_use.sel(nodes="X2", techs="pv") == approx(830.064659)

        assert float(model.results.flow_export.sum()) == approx(122.7156)

        # GLPK doesn't agree with commercial solvers, so we have to account for that here
        cost_sum = 430.097399 if solver == "glpk" else 430.089188
        assert float(model.results.cost.sum()) == approx(cost_sum)

    def test_urban_example_results_area(self):
        self.example_tester("per_area")

    def test_urban_example_results_area_gurobi(self):
        pytest.importorskip("gurobipy")
        self.example_tester("per_area", solver="gurobi", solver_io="python")

    def test_urban_example_results_cap(self):
        self.example_tester("per_cap")

    def test_urban_example_results_cap_gurobi(self):
        pytest.importorskip("gurobipy")
        self.example_tester("per_cap", solver="gurobi", solver_io="python")

    @pytest.mark.filterwarnings("ignore:(?s).*Integer:calliope.exceptions.ModelWarning")
    def test_milp_example_results(self):
        model = calliope.examples.milp(
            override_dict={
                "model.subset_time": ["2005-01-01", "2005-01-01"],
                "run.solver_options.mipgap": 0.001,
            }
        )
        model.build()
        model.solve()

        assert model.results.flow_cap.sel(nodes="X1", techs="chp") == 300
        assert model.results.flow_cap.sel(nodes="X2", techs="heat_pipes:N1") == approx(
            188.363137
        )

        assert model.results.flow_out.sum("timesteps").sel(
            carriers="gas", nodes="X1", techs="supply_gas"
        ) == approx(12363.173036)
        assert float(model.results.flow_export.sum()) == approx(0)

        assert model.results.purchased.sel(nodes="X2", techs="boiler") == 1
        assert model.results.units.sel(nodes="X1", techs="chp") == 1

        assert float(model.results.operating_units.sum()) == 24

        assert float(model.results.cost.sum()) == approx(540.780779)

    @pytest.mark.xfail(reason="Not expecting operate mode to work at the moment")
    def test_operate_example_results(self):
        model = calliope.examples.operate(
            override_dict={"model.subset_time": ["2005-07-01", "2005-07-04"]}
        )
        with pytest.warns(calliope.exceptions.ModelWarning) as excinfo:
            model.build()

        expected_warnings = [
            "Flow capacity constraint removed",
            "Source capacity constraint defined and set to infinity for all supply_plus techs",
            "Storage cannot be cyclic in operate run mode, setting `run.cyclic_storage` to False for this run",
        ]

        assert check_error_or_warning(excinfo, expected_warnings)

        assert all(
            model.results.timesteps
            == pd.date_range("2005-07", "2005-07-04 23:00:00", freq="H")
        )