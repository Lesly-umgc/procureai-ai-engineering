import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score, accuracy_score
from database.db import SessionLocal, Invoice, PurchaseOrder, Vendor

# Tabular features the XGBoost model trains on. Kept module-level so the
# training path, the proof scripts, and the evals all use identical features.
FEATURE_COLUMNS = ['subtotal', 'total_amount', 'amount_limit', 'risk_rating',
                   'po_ratio', 'threshold_proximity', 'tax_ratio']


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the tabular anomaly features used by the XGBoost model."""
    df = df.copy()
    df['po_ratio'] = df['total_amount'] / (df['amount_limit'] + 1e-5)
    df['threshold_proximity'] = (df['total_amount'] - 10000.0).abs()  # split-PO / threshold dodging
    # Implied tax rate from the totals (NOT tax_amount/subtotal): this is what
    # detects CALC_DISCREPANCY, where total != subtotal + tax. Must match
    # score_invoice() below — a past inconsistency here silently blinded the model.
    df['tax_ratio'] = (df['total_amount'] - df['subtotal']) / (df['subtotal'] + 1e-5)
    return df

class AnomalyScoringEngine:
    def __init__(self):
        self.model = None

    def extract_features_from_db(self, limit=10000):
        """
        Extracts tabular features from PostgreSQL in chunks to respect memory limits.
        """
        db = SessionLocal()
        try:
            query = f"""
                SELECT 
                    i.invoice_id,
                    i.subtotal,
                    i.tax_amount,
                    i.total_amount,
                    po.amount_limit,
                    v.risk_rating,
                    CASE WHEN i.status = 'FLAGGED' THEN 1 ELSE 0 END as is_fraud
                FROM invoices i
                JOIN purchase_orders po ON i.po_id = po.po_id
                JOIN vendors v ON i.vendor_id = v.vendor_id
                LIMIT {limit};
            """
            df = pd.read_sql(query, db.bind)
            return df
        finally:
            db.close()

    def train_dataframe(self, df: pd.DataFrame, label_col: str = "is_fraud") -> dict:
        """Train on an in-memory DataFrame (used by train_model and by proofs/)."""
        df = engineer_features(df)

        if df.empty or len(df) < 100:
            print("Not enough data to train model.")
            return {}

        X = df[FEATURE_COLUMNS]
        y = df[label_col]

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y)

        neg_count = (y_train == 0).sum()
        pos_count = (y_train == 1).sum()
        scale_weight = neg_count / (pos_count + 1e-5)

        print(f"Training XGBoost classifier...")
        # Hyperparameters tuned on the synthetic pipeline (proofs/prove_accuracy.py):
        # scale_pos_weight was removed — it inflated false positives and *lowered*
        # accuracy (94.4% -> 97.8% without it). Ghost-vendor and duplicate fraud
        # are handled by deterministic rules (see core/rules.py), not the model.
        self.model = xgb.XGBClassifier(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            random_state=42
        )
        self.model.fit(X_train, y_train)

        preds = self.model.predict(X_test)
        probs = self.model.predict_proba(X_test)[:, 1]
        metrics = {
            "accuracy": float(accuracy_score(y_test, preds)),
            "roc_auc": float(roc_auc_score(y_test, probs)),
            "n_train": int(len(X_train)),
            "n_test": int(len(X_test)),
            "report": classification_report(y_test, preds, output_dict=True),
        }
        print(f"XGBoost accuracy: {metrics['accuracy']:.4f} | ROC AUC: {metrics['roc_auc']:.4f}")
        print(classification_report(y_test, preds))
        return metrics

    def train_model(self):
        print("Extracting training dataset from PostgreSQL...")
        df = self.extract_features_from_db(limit=50000)
        return self.train_dataframe(df)

    def score_invoice(self, subtotal: float, total_amount: float, amount_limit: float, risk_rating: float) -> float:
        if self.model is None:
            # Fallback heuristic rule scoring if model not trained
            po_ratio = total_amount / (amount_limit + 1e-5)
            score = 0.1
            if po_ratio > 0.95 and po_ratio <= 1.0:
                score = 0.85 # Split PO / threshold dodging
            elif risk_rating > 0.7:
                score = 0.90
            elif total_amount > amount_limit:
                score = 0.95
            return float(score)

        po_ratio = total_amount / (amount_limit + 1e-5)
        threshold_proximity = np.abs(total_amount - 10000.0)
        # Implied tax rate — consistent with engineer_features() used in training.
        tax_ratio = (total_amount - subtotal) / (subtotal + 1e-5)

        X_new = pd.DataFrame([[subtotal, total_amount, amount_limit, risk_rating, po_ratio, threshold_proximity, tax_ratio]],
                             columns=['subtotal', 'total_amount', 'amount_limit', 'risk_rating', 'po_ratio', 'threshold_proximity', 'tax_ratio'])
        prob = self.model.predict_proba(X_new)[0][1]
        return float(prob)

if __name__ == "__main__":
    engine = AnomalyScoringEngine()
    engine.train_model()
