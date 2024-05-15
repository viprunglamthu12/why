"""This module contains implementations of different anomaly scorers."""

from typing import Optional

import numpy as np
from statsmodels.robust import mad

from dowhy.gcm.anomaly_scorer import AnomalyScorer
from dowhy.gcm.constant import EPS
from dowhy.gcm.density_estimator import DensityEstimator
from dowhy.gcm.util.general import shape_into_2d


class MedianCDFQuantileScorer(AnomalyScorer):
    """Given an anomalous observation x and samples from the distribution of X, this score represents:
        score(x) = 1 - 2 * min[P(X > x) + P(X = x) / 2, P(X < x) + P(X = x) / 2]

    Comparing two NaN values are considered equal here.

    It scores the observation based on the quantile of x with respect to the distribution of X. Here, if the
    sample x lies in the tail of the distribution, we want to have a large score. Since we apriori don't know
    whether the sample falls on the left or right side of the median of X, we estimate the quantile on both
    sides and take the minimum. Here, these probabilities are estimated by counting and since half of the samples are
    on one side from the median, we need to multiply this by a factor of two to obtain the two-sided quantile.
    For example:
        X = [-3, -2, -1, 0, 1, 2, 3]
        x = 2.5
    Then, x falls in the right sided-quantile and only one sample in X is larger than x. Therefore, we get
        p(X >= x) = 1 / 7
        P(X <= x) = 6 / 7
    With the end score of:
       1 - 2 * min[P(X > x) + P(X = x) / 2, P(X < x) + P(X = x) / 2] = 1 - 2 / 7 = 0.71

    Note: For equal samples, we contribute half of the count to the left and half of the count the right side.

    The higher the score, the less likely the sample comes from the distribution of X.
    """

    def __init__(self):
        self._distribution_samples = None

    def fit(self, X: np.ndarray) -> None:
        if (X.ndim == 2 and X.shape[1] > 1) or X.ndim > 2:
            raise ValueError("The MedianCDFQuantileScorer currently only supports one-dimensional data!")

        self._distribution_samples = X.reshape(-1).astype(float)

    def score(self, X: np.ndarray) -> np.ndarray:
        if self._distribution_samples is None:
            raise ValueError("Scorer has not been fitted!")

        X = shape_into_2d(X.astype(float))

        equal_samples = np.sum(np.isclose(X, self._distribution_samples, rtol=0, atol=0, equal_nan=True), axis=1)
        greater_samples = np.sum(X > self._distribution_samples, axis=1) + equal_samples / 2
        smaller_samples = np.sum(X < self._distribution_samples, axis=1) + equal_samples / 2

        return (
            1 - 2 * np.amin(np.vstack([greater_samples, smaller_samples]), axis=0) / self._distribution_samples.shape[0]
        )


class RescaledMedianCDFQuantileScorer(AnomalyScorer):
    """Given an anomalous observation x and samples from the distribution of X, this score represents:
        score(x) = -log(2 * min[P(X > x) + P(X = x) / 2, P(X < x) + P(X = x) / 2])

    Comparing two NaN values are considered equal here.

    This is a rescaled version of the score s obtained by the :class:`~dowhy.gcm.anomaly_scorers.MedianCDFQuantileScorer`
    by calculating the negative log-probability -log(1 - s). This has the advantage that small differences in the
    probabilities are amplified, especially when they are close to 0. For instance, the difference between
    probabilities 0.02 and 0.01 seems to be small and insignificant, but the rescaled difference would be significantly
    larger: -log(0.02) - log(0.01) −= 8.5

    The higher the score, the less likely the sample comes from the distribution of X.
    """

    def __init__(self):
        self._scorer = MedianCDFQuantileScorer()

    def fit(self, X: np.ndarray) -> None:
        self._scorer.fit(X)

    def score(self, X: np.ndarray) -> np.ndarray:
        scores = 1 - self._scorer.score(X)
        scores[scores == 0] = EPS

        return -np.log(scores)


class ITAnomalyScorer(AnomalyScorer):
    """Transforms any anomaly scorer into an information theoretic (IT) score. This means, given a scorer S(x), an
    anomalous observation x and samples from the distribution of X, this scorer class represents:
        score(x) = -log(P(S(X) >= S(x)))

    This is, the negative logarithm of the probability to get the same or a higher score with (random) samples from X
    compared to the score obtained based on the anomalous observation x. By this, the score of arbitrarily different
    anomaly scorers become comparable information theoretic quantities. The new score -log(P(S(X) >= S(x))) can also
    be seen as "The higher the score, the rarer the anomaly event". For instance, if we have S(x) = c, but observe
    the same or higher scores in 50% or even 100% of all samples in X, then this is not really a rare event, and thus,
    not an anomaly. As mentioned above, transforming it into an IT score makes arbitrarily different anomaly scorer
    with potentially completely different scaling comparable. For example, one could compare the IT score of
    isolation forests with z-scores.

    For more details about IT scores, see:
        Causal structure based root cause analysis of outliers
        Kailash Budhathoki, Patrick Bloebaum, Lenon Minorics, Dominik Janzing (2022)

    The higher the score, the higher the likelihood that the observations is an anomaly.
    """

    def __init__(self, anomaly_scorer: AnomalyScorer):
        self._anomaly_scorer = anomaly_scorer
        self._distribution_samples = None
        self._scores_of_distribution_samples = None

    def fit(self, X: np.ndarray) -> None:
        self._distribution_samples = shape_into_2d(X)
        self._anomaly_scorer.fit(self._distribution_samples)
        self._scores_of_distribution_samples = self._anomaly_scorer.score(self._distribution_samples).reshape(-1)

    def score(self, X: np.ndarray) -> np.ndarray:
        X = shape_into_2d(X)
        scores_of_samples_to_score = self._anomaly_scorer.score(X).reshape(-1, 1)
        return -np.log(
            (np.sum(self._scores_of_distribution_samples >= scores_of_samples_to_score, axis=1) + 0.5)
            / (self._scores_of_distribution_samples.shape[0] + 0.5)
        )


class MeanDeviationScorer(AnomalyScorer):
    """Given an anomalous observation x and samples from the distribution of X, this score represents:
        score(x) = |x - E[X]| / std[X]

    This scores the given sample based on its distance to the mean of X and scaled by the standard deviation of X. This
    is also equivalent to the Z-score in Gaussian variables.

    The higher the score, the higher the deviation of the observation from the mean of X.
    """

    def __init__(self):
        self._mean = None
        self._std = None

    def fit(self, X: np.ndarray) -> None:
        self._mean = np.mean(X)
        self._std = np.std(X)

    def score(self, X: np.ndarray) -> np.ndarray:
        if self._mean is None or self._std is None:
            raise ValueError("Scorer has not been fitted!")

        return abs(X - self._mean) / self._std


class MedianDeviationScorer(AnomalyScorer):
    """Given an anomalous observation x and samples from the distribution of X, this score represents:
        score(x) = |x - med[X]| / mad[X]

    This scores the given sample based on its distance to the median of X and scaled by the median absolute deviation
    of X.

    The higher the score, the higher the deviation of the observation from the median of X.
    """

    def __init__(self):
        self._median = None
        self._mad = None

    def fit(self, X: np.ndarray) -> None:
        self._median = np.median(X)
        self._mad = mad(X)

    def score(self, X: np.ndarray) -> np.ndarray:
        if self._median is None or self._mad is None:
            raise ValueError("Scorer has not been fitted!")

        return abs(X - self._median) / self._mad


class InverseDensityScorer(AnomalyScorer):
    """Estimates an anomaly score based on 1 / p(x), where x is the data to score. The density value p(x) is estimated
    using the given density estimator. If None is given, a Gaussian mixture model is used by default.

    Note: The given density estimator needs to support the data types, i.e. if the data has categorical values, the
    density estimator needs to be able to handle that. The default Gaussian model can only handle numeric data.

    Note: If the density p(x) is 0, a nan or inf could be returned.
    """

    def __init__(self, density_estimator: Optional[DensityEstimator] = None):
        if density_estimator is None:
            from dowhy.gcm.density_estimators import GaussianMixtureDensityEstimator

            density_estimator = GaussianMixtureDensityEstimator()
        self._density_estimator = density_estimator
        self._fitted = False

    def fit(self, X: np.ndarray) -> None:
        self._density_estimator.fit(X)
        self._fitted = True

    def score(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise ValueError("Scorer has not been fitted!")

        return 1 / self._density_estimator.density(X)
