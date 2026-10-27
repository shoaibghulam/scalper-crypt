"""Optional training-only trade filter. JSON trees, never untrusted pickle."""

import numpy as np


def fit_filter(features, training_result, seed):
    from sklearn.ensemble import RandomForestClassifier

    matrix = features.ml_matrix()
    trades = training_result["trades"]
    indices = trades[:, 0].astype(int) - 1
    valid = np.isfinite(matrix[indices]).all(axis=1)
    x, y = matrix[indices][valid], (trades[:, 5][valid] > 0).astype(int)
    if len(y) < 40 or len(np.unique(y)) < 2:
        return None, "Not fitted: requires 40 completed training trades and both outcomes"
    model = RandomForestClassifier(
        n_estimators=64, max_depth=4, min_samples_leaf=8, random_state=seed, n_jobs=1
    )
    model.fit(x, y)
    trees = []
    for estimator in model.estimators_:
        t = estimator.tree_
        values = t.value[:, 0, :]
        probability = values[:, 1] / values.sum(axis=1)
        trees.append(
            {
                "left": t.children_left.tolist(),
                "right": t.children_right.tolist(),
                "feature": t.feature.tolist(),
                "threshold": t.threshold.tolist(),
                "probability": probability.tolist(),
            }
        )
    return {
        "kind": "random_forest_v1",
        "threshold": 0.55,
        "trees": trees,
        "training_trades": len(y),
        "trained_through": training_result["last"],
        "probability_note": "Uncalibrated training model estimate; not a confidence guarantee",
    }, "Fitted on completed training trades only"


def predict(model, matrix):
    result = np.zeros(len(matrix))
    valid = np.isfinite(matrix).all(axis=1)
    for tree in model["trees"]:
        nodes = np.zeros(len(matrix), dtype=int)
        left, right = np.array(tree["left"]), np.array(tree["right"])
        feature, threshold = np.array(tree["feature"]), np.array(tree["threshold"])
        for _ in range(5):
            active = left[nodes] >= 0
            rows = np.flatnonzero(active)
            if not len(rows):
                break
            node = nodes[rows]
            nodes[rows] = np.where(
                matrix[rows, feature[node]] <= threshold[node], left[node], right[node]
            )
        result += np.array(tree["probability"])[nodes]
    result /= len(model["trees"])
    result[~valid] = 0
    return result
