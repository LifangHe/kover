#!/usr/bin/env python
"""
	Kover: Learn interpretable computational phenotyping models from k-merized genomic data
	Copyright (C) 2015  Alexandre Drouin

	This program is free software: you can redistribute it and/or modify
	it under the terms of the GNU General Public License as published by
	the Free Software Foundation, either version 3 of the License, or
	(at your option) any later version.

	This program is distributed in the hope that it will be useful,
	but WITHOUT ANY WARRANTY; without even the implied warranty of
	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
	GNU General Public License for more details.

	You should have received a copy of the GNU General Public License
	along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import logging
import numpy as np

from functools import partial

from .models import conjunction, ConjunctionModel, disjunction, DisjunctionModel
from ...utils import _class_to_string

class BaseSetCoveringMachine(object):
    def __init__(self, model_type, max_rules):
        if model_type == conjunction:
            self._add_rule_to_model = self._append_conjunction_model
            self.model_type = conjunction
        elif model_type == disjunction:
            self._add_rule_to_model = self._append_disjunction_model
            self.model_type = disjunction
        else:
            raise ValueError("Unsupported model type.")

        self.max_rules = max_rules
        self._flags = {}
        super(BaseSetCoveringMachine, self).__init__()

    def fit(self, rules, rule_classifications, positive_example_idx, negative_example_idx, rule_blacklist=None,
            tiebreaker=None, iteration_callback=None, **kwargs):
        """
        TODO
        """
        utility_function_additional_args = {}
        if kwargs != None:
            for key, value in kwargs.iteritems():
                if key[:9] == "utility__":
                    utility_function_additional_args[key[9:]] = value

        if self.model_type == disjunction:
            # Switch the example labels
            tmp = positive_example_idx
            positive_example_idx = negative_example_idx
            negative_example_idx = tmp

        logging.debug("Got " + str(len(rules)) + " binary rules.")
        if rule_classifications.shape[1] != len(rules):
            raise ValueError("The number of rules must match between rule_classifications and rules.")

        # Validate the rule blacklist
        if rule_blacklist is not None:
            rule_blacklist = np.unique(rule_blacklist)
            if len(rule_blacklist) == rule_classifications.shape[1]:
                raise ValueError("The blacklist cannot include all the rules.")
            logging.debug("The following rules are blacklisted and will not be considered:" + str(rule_blacklist))

        training_example_idx = np.hstack((positive_example_idx, negative_example_idx))  # Needed for rule importances
        model_rules_idx = []  # Contains the index of the rules in the model
        while len(negative_example_idx) > 0 and len(self.model) < self.max_rules:
            iteration_info = {"iteration_number": len(self.model) + 1}

            utilities, \
            positive_error_count, \
            negative_cover_count = self._get_binary_rule_utilities(
                rule_classifications=rule_classifications,
                positive_example_idx=positive_example_idx,
                negative_example_idx=negative_example_idx,
                **utility_function_additional_args)

            # Exclude the rules in the blacklist
            if rule_blacklist is not None:
                utilities[rule_blacklist] = -np.infty

            # Find all the indexes of all rules with the best utility
            iteration_info["utility_max"] = np.max(utilities)
            iteration_info["utility_argmax"] = np.where(utilities == iteration_info["utility_max"])[0]
            logging.debug("Greatest utility is %.5f" % iteration_info["utility_max"])
            logging.debug("There are %d rules with the same utility." % len(iteration_info["utility_argmax"]))
            del utilities

            # Do not select rules that cover no negative examples and make errors on no positive examples
            best_utility_idx = iteration_info["utility_argmax"][np.logical_or(negative_cover_count[iteration_info["utility_argmax"]] != 0, positive_error_count[iteration_info["utility_argmax"]] != 0)]
            del positive_error_count, negative_cover_count
            if len(best_utility_idx) == 0:
                logging.debug("The rule of maximal utility does not cover negative examples or make errors" +
                                    " on positive examples. It will not be added to the model. Stopping here.")
                break

            # Apply a user-specified tiebreaker if necessary
            if len(best_utility_idx) == 1:
                best_rule_idx = best_utility_idx[0]
                iteration_info["equivalent_rules_idx"] = np.array([best_rule_idx])
            elif len(best_utility_idx) > 1:
                best_rule_idx = tiebreaker(best_utility_idx)
                logging.debug("The tiebreaker returned %d equivalent rules." % len(best_rule_idx))
                iteration_info["equivalent_rules_idx"] = best_rule_idx
                best_rule_idx = best_rule_idx[0]  # If many are equivalent, just take the first one.
            del best_utility_idx

            # Add the best rule to the model
            iteration_info["selected_rule"] = self._add_rule_to_model(rules[best_rule_idx])
            model_rules_idx.append(best_rule_idx)

            # Get the best rule's classification for each example
            # XXX: This includes the testing examples, but we don't consider them. Otherwise the idx would be invalid.
            best_rule_classifications = rule_classifications.get_columns(best_rule_idx)

            # Discard examples predicted as negative
            logging.debug("Discarding covered negative examples")
            negative_example_idx = negative_example_idx[best_rule_classifications[negative_example_idx] != 0]
            logging.debug("Discarding misclassified positive examples")
            positive_example_idx = positive_example_idx[best_rule_classifications[positive_example_idx] != 0]
            logging.debug("Remaining negative examples:" + str(len(negative_example_idx)))
            logging.debug("Remaining positive examples:" + str(len(positive_example_idx)))

            if iteration_callback is not None:
                iteration_callback(iteration_info)

        # Compute the rule importances
        model_rule_classifications = rule_classifications.get_columns(model_rules_idx)[training_example_idx]
        model_neg_prediction_idx = np.where(np.prod(model_rule_classifications, axis=1) == 0)[0]
        self.rule_importances = (float(len(model_neg_prediction_idx)) - \
                                model_rule_classifications[model_neg_prediction_idx].sum(axis=0)) / \
                                len(model_neg_prediction_idx)

    def predict(self, X):
        """
        Compute binary predictions.
        Parameters:
        -----------
        X: numpy_array, shape=(n_examples,)
            The feature vectors associated to some examples.
        Returns:
        --------
        predictions: numpy_array, shape=(n_examples,)
            The predicted class for each example.
        """
        return self._predict(X)

    def _append_conjunction_model(self, new_rule):
        self.model.add(new_rule)
        logging.debug("Rule added to the model: " + str(new_rule))
        return new_rule

    def _append_disjunction_model(self, new_rule):
        new_rule = new_rule.inverse()
        self.model.add(new_rule)
        logging.debug("Rule added to the model: " + str(new_rule))
        return new_rule

    def _is_fitted(self):
        return len(self.model) > 0

    def _predict(self, X):
        if not self._is_fitted():
            raise RuntimeError("A model must be fitted prior to calling predict.")
        return self.model.predict(X)

    def _predict_proba(self, X):
        """
        Child classes must have the PROBABILISTIC_PREDICTIONS set to True to use this method.
        """
        if not self._is_fitted():
            raise RuntimeError("A model must be fitted prior to calling predict.")

        if not self._flags.get("PROBABILISTIC_PREDICTIONS", False):
            raise RuntimeError("The predictor does not support probabilistic predictions.")

        return self.model.predict_proba(X)

    def __str__(self):
        return _class_to_string(self)

class SetCoveringMachine(BaseSetCoveringMachine):
    """
    The Set Covering Machine (SCM).
    Marchand, M., & Taylor, J. S. (2003). The set covering machine. Journal of Machine Learning Research, 3, 723–746.
    Parameters:
    -----------
    model_type: pyscm.model.conjunction or pyscm.model.disjunction, default=pyscm.model.conjunction
        The type of model to be built.
    p: float, default=1.0
        A parameter to control the importance of making prediction errors on positive examples in the utility function.
    max_rules: int, default=10
        The maximum number of binary rules to include in the model.
    verbose: bool, default=False
        Sets verbose mode on/off.
    """

    def __init__(self, model_type=conjunction, p=1.0, max_rules=10):
        super(SetCoveringMachine, self).__init__(model_type=model_type, max_rules=max_rules)

        if model_type == conjunction:
            self.model = ConjunctionModel()
        elif model_type == disjunction:
            self.model = DisjunctionModel()
        else:
            raise ValueError("Unsupported model type.")

        self.p = p

    def _get_binary_rule_utilities(self, rule_classifications, positive_example_idx, negative_example_idx):
        logging.debug("Counting covered negative examples")
        negative_cover_counts = negative_example_idx.shape[0] - rule_classifications.sum_rows(negative_example_idx)

        logging.debug("Counting errors on positive examples")
        # It is possible that there are no more positive examples to be considered. This is not possible for negative
        # examples because of the SCM's stopping criterion.
        if positive_example_idx.shape[0] > 0:
            positive_error_counts = positive_example_idx.shape[0] - rule_classifications.sum_rows(positive_example_idx)
        else:
            positive_error_counts = np.zeros(rule_classifications.shape[1], dtype=negative_cover_counts.dtype)

        logging.debug("Computing rule utilities")
        utilities = negative_cover_counts - float(self.p) * positive_error_counts

        return utilities, positive_error_counts, negative_cover_counts