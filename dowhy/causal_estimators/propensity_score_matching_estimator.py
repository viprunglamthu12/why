from typing import Any, List, Optional, Union

import pandas as pd
from sklearn.neighbors import NearestNeighbors

from dowhy.causal_estimator import CausalEstimate, CausalEstimator
from dowhy.causal_estimators.propensity_score_estimator import PropensityScoreEstimator
from dowhy.causal_identifier import IdentifiedEstimand


class PropensityScoreMatchingEstimator(PropensityScoreEstimator):
    """Estimate effect of treatment by finding matching treated and control
    units based on propensity score.

    Straightforward application of the back-door criterion.

    Supports additional parameters as listed below.

    """

    def __init__(
        self,
        identified_estimand: IdentifiedEstimand,
        test_significance: Union[bool, str] = False,
        evaluate_effect_strength: bool = False,
        confidence_intervals: bool = False,
        num_null_simulations: int = CausalEstimator.DEFAULT_NUMBER_OF_SIMULATIONS_STAT_TEST,
        num_simulations: int = CausalEstimator.DEFAULT_NUMBER_OF_SIMULATIONS_CI,
        sample_size_fraction: int = CausalEstimator.DEFAULT_SAMPLE_SIZE_FRACTION,
        confidence_level: float = CausalEstimator.DEFAULT_CONFIDENCE_LEVEL,
        need_conditional_estimates: Union[bool, str] = "auto",
        num_quantiles_to_discretize_cont_cols: int = CausalEstimator.NUM_QUANTILES_TO_DISCRETIZE_CONT_COLS,
        propensity_score_model: Optional[Any] = None,
        propensity_score_column: str = "propensity_score",
        **kwargs,
    ):
        """
        :param identified_estimand: probability expression
            representing the target identified estimand to estimate.
        :param test_significance: Binary flag or a string indicating whether to test significance and by which method. All estimators support test_significance="bootstrap" that estimates a p-value for the obtained estimate using the bootstrap method. Individual estimators can override this to support custom testing methods. The bootstrap method supports an optional parameter, num_null_simulations. If False, no testing is done. If True, significance of the estimate is tested using the custom method if available, otherwise by bootstrap.
        :param evaluate_effect_strength: (Experimental) whether to evaluate the strength of effect
        :param confidence_intervals: Binary flag or a string indicating whether the confidence intervals should be computed and which method should be used. All methods support estimation of confidence intervals using the bootstrap method by using the parameter confidence_intervals="bootstrap". The bootstrap method takes in two arguments (num_simulations and sample_size_fraction) that can be optionally specified in the params dictionary. Estimators may also override this to implement their own confidence interval method. If this parameter is False, no confidence intervals are computed. If True, confidence intervals are computed by the estimator's specific method if available, otherwise through bootstrap
        :param num_null_simulations: The number of simulations for testing the
            statistical significance of the estimator
        :param num_simulations: The number of simulations for finding the
            confidence interval (and/or standard error) for a estimate
        :param sample_size_fraction: The size of the sample for the bootstrap
            estimator
        :param confidence_level: The confidence level of the confidence
            interval estimate
        :param need_conditional_estimates: Boolean flag indicating whether
            conditional estimates should be computed. Defaults to True if
            there are effect modifiers in the graph
        :param num_quantiles_to_discretize_cont_cols: The number of quantiles
            into which a numeric effect modifier is split, to enable
            estimation of conditional treatment effect over it.
        :param propensity_score_model: Model used to compute propensity score.
            Can be any classification model that supports fit() and
            predict_proba() methods. If None, LogisticRegression is used.
        :param propensity_score_column: Column name that stores the
            propensity score. Default='propensity_score'
        :param kwargs: (optional) Additional estimator-specific parameters

        """
        super().__init__(
            identified_estimand=identified_estimand,
            test_significance=test_significance,
            evaluate_effect_strength=evaluate_effect_strength,
            confidence_intervals=confidence_intervals,
            num_null_simulations=num_null_simulations,
            num_simulations=num_simulations,
            sample_size_fraction=sample_size_fraction,
            confidence_level=confidence_level,
            need_conditional_estimates=need_conditional_estimates,
            num_quantiles_to_discretize_cont_cols=num_quantiles_to_discretize_cont_cols,
            propensity_score_model=propensity_score_model,
            propensity_score_column=propensity_score_column,
            **kwargs,
        )

        self.logger.info("INFO: Using Propensity Score Matching Estimator")

    def fit(
        self,
        data: pd.DataFrame,
        effect_modifier_names: Optional[List[str]] = None,
    ):
        """
        Fits the estimator with data for effect estimation
        :param data: data frame containing the data
        :param treatment: name of the treatment variable
        :param outcome: name of the outcome variable
        :param effect_modifiers: Variables on which to compute separate
                    effects, or return a heterogeneous effect function. Not all
                    methods support this currently.
        """
        super().fit(data, effect_modifier_names=effect_modifier_names)
        self.symbolic_estimator = self.construct_symbolic_estimator(self._target_estimand)
        self.logger.info(self.symbolic_estimator)

        return self

    def estimate_effect(
        self,
        data: pd.DataFrame,
        treatment_value: Any = 1,
        control_value: Any = 0,
        target_units=None,
        **_,
    ):
        self._target_units = target_units
        self._treatment_value = treatment_value
        self._control_value = control_value
        if self.propensity_score_column not in data:
            self.estimate_propensity_score_column(data)

        # this assumes a binary treatment regime
        treated = data.loc[data[self._target_estimand.treatment_variable[0]] == 1]
        control = data.loc[data[self._target_estimand.treatment_variable[0]] == 0]

        # TODO remove neighbors that are more than a given radius apart

        # estimate ATT on treated by summing over difference between matched neighbors
        control_neighbors = NearestNeighbors(n_neighbors=1, algorithm="ball_tree").fit(
            control[self.propensity_score_column].values.reshape(-1, 1)
        )
        distances, indices = control_neighbors.kneighbors(treated[self.propensity_score_column].values.reshape(-1, 1))
        self.logger.debug("distances:")
        self.logger.debug(distances)

        att = 0
        numtreatedunits = treated.shape[0]
        for i in range(numtreatedunits):
            treated_outcome = treated.iloc[i][self._target_estimand.outcome_variable[0]].item()
            control_outcome = control.iloc[indices[i]][self._target_estimand.outcome_variable[0]].item()
            att += treated_outcome - control_outcome

        att /= numtreatedunits

        # Now computing ATC
        treated_neighbors = NearestNeighbors(n_neighbors=1, algorithm="ball_tree").fit(
            treated[self.propensity_score_column].values.reshape(-1, 1)
        )
        distances, indices = treated_neighbors.kneighbors(control[self.propensity_score_column].values.reshape(-1, 1))
        atc = 0
        numcontrolunits = control.shape[0]
        for i in range(numcontrolunits):
            control_outcome = control.iloc[i][self._target_estimand.outcome_variable[0]].item()
            treated_outcome = treated.iloc[indices[i]][self._target_estimand.outcome_variable[0]].item()
            atc += treated_outcome - control_outcome

        atc /= numcontrolunits

        if target_units == "att":
            est = att
        elif target_units == "atc":
            est = atc
        elif target_units == "ate":
            est = (att * numtreatedunits + atc * numcontrolunits) / (numtreatedunits + numcontrolunits)
        else:
            raise ValueError("Target units string value not supported")

        estimate = CausalEstimate(
            data=data,
            treatment_name=self._target_estimand.treatment_variable,
            outcome_name=self._target_estimand.outcome_variable,
            estimate=est,
            control_value=control_value,
            treatment_value=treatment_value,
            target_estimand=self._target_estimand,
            realized_estimand_expr=self.symbolic_estimator,
            propensity_scores=data[self.propensity_score_column],
        )

        estimate.add_estimator(self)
        return estimate

    def construct_symbolic_estimator(self, estimand):
        expr = "b: " + ", ".join(estimand.outcome_variable) + "~"
        # TODO -- fix: we are actually conditioning on positive treatment (d=1)
        var_list = estimand.treatment_variable + estimand.get_backdoor_variables()
        expr += "+".join(var_list)
        return expr
