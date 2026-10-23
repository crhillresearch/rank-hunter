"""JSON-defined elliptic families for v0.4.

This adapter exists so a published/high-rank fibration can be installed as data
rather than requiring another search-engine code change.

Schema (expressions are trusted local Sage expressions in the declared rational-base parameter):

{
  "name": "display name",
  "generic_rank": 17,
  "parameter": "t",
  "a_invariants": ["a1(t)", "a2(t)", "a3(t)", "a4(t)", "a6(t)"],
  "sections": [
    {"x": "x1(t)", "y": "y1(t)"},
    ...
  ]
}

Expressions may be rational functions.  Specializations at poles or singular
fibres return ``None`` from ``curve``/``curve_mod_p``.
"""

from __future__ import annotations

import json
from pathlib import Path

from sage.all import QQ, GF, EllipticCurve, PolynomialRing, sage_eval


class FormulaFamily:
    def __init__(self, data, *, source_path=None):
        self.data = dict(data)
        self.source_path = Path(source_path).resolve() if source_path else None
        self._name = str(self.data["name"])
        self._generic_rank = (
            None if self.data.get("generic_rank") is None
            else int(self.data["generic_rank"])
        )
        self.parameter = str(self.data.get("parameter") or "t").strip()
        if not self.parameter.isidentifier():
            raise ValueError("formula family parameter must be a valid identifier")
        ainvs = self.data.get("a_invariants")
        if not isinstance(ainvs, list) or len(ainvs) != 5:
            raise ValueError("formula family requires five a_invariants expressions")
        self._ainv_exprs = [str(x) for x in ainvs]
        self._sections = list(self.data.get("sections") or [])

    @classmethod
    def from_json(cls, path):
        path = Path(path)
        return cls(json.loads(path.read_text()), source_path=path)

    def name(self):
        return self._name

    def generic_rank(self):
        return self._generic_rank

    def _eval(self, expr, t):
        return sage_eval(str(expr), locals={self.parameter: t})

    def _ainvs_at(self, t):
        return [self._eval(expr, t) for expr in self._ainv_exprs]

    def curve(self, t):
        try:
            tq = QQ(t)
            E = EllipticCurve(QQ, [QQ(x) for x in self._ainvs_at(tq)])
            if E.discriminant() == 0:
                return None
            return E
        except (ArithmeticError, ValueError, ZeroDivisionError):
            return None

    def curve_mod_p(self, r, p):
        try:
            F = GF(int(p))
            # Evaluate rational coefficients over Q first.  Sage cannot
            # multiply a QQ literal by a finite-field parameter directly;
            # reducing the specialized coefficients afterwards also rejects
            # precisely the residues with a denominator divisible by p.
            tr = QQ(int(r) % int(p))
            ainvs = [F(x) for x in self._ainvs_at(tr)]
            E = EllipticCurve(F, ainvs)
            if E.discriminant() == 0:
                return None
            return E
        except (ArithmeticError, ValueError, ZeroDivisionError, TypeError):
            return None

    def generic_section_points(self, t):
        E = self.curve(t)
        if E is None:
            return []
        tq = QQ(t)
        out = []
        for i, rec in enumerate(self._sections, 1):
            if isinstance(rec, dict):
                xexpr, yexpr = rec["x"], rec["y"]
            else:
                xexpr, yexpr = rec
            try:
                x = QQ(self._eval(xexpr, tq))
                y = QQ(self._eval(yexpr, tq))
                out.append(E(x, y))
            except Exception as exc:
                raise ValueError(
                    f"section {i} failed at t={tq}: {exc}"
                ) from exc
        return out

    def generic_section_metadata(self, t):
        """Return provenance metadata parallel to :meth:`generic_section_points`.

        JSON section records may carry arbitrary provenance fields in addition
        to x/y.  Exact specialized source coordinates are always attached so
        downstream MW/REA/saturation work can retain the original family trace.
        """
        tq = QQ(t)
        out = []
        for index, rec in enumerate(self._sections, 1):
            meta = {"section_index": index - 1}
            if isinstance(rec, dict):
                for key, value in rec.items():
                    if key not in {"x", "y"}:
                        meta[str(key)] = value
                try:
                    meta["trace_coordinates"] = [
                        str(QQ(self._eval(rec["x"], tq))),
                        str(QQ(self._eval(rec["y"], tq))),
                    ]
                except Exception:
                    pass
            out.append(meta)
        return out

    def validate_symbolically(self):
        """Check every declared section over the rational function field exactly."""
        R = PolynomialRing(QQ, self.parameter)
        K = R.fraction_field()
        t = K(R.gen())
        ainvs = [K(self._eval(expr, t)) for expr in self._ainv_exprs]
        E = EllipticCurve(K, ainvs)
        points = []
        for i, rec in enumerate(self._sections, 1):
            if isinstance(rec, dict):
                xexpr, yexpr = rec["x"], rec["y"]
            else:
                xexpr, yexpr = rec
            x = K(self._eval(xexpr, t))
            y = K(self._eval(yexpr, t))
            try:
                points.append(E(x, y))
            except Exception as exc:
                raise ValueError(f"section {i} is not on the generic curve") from exc
        return {
            "name": self.name(),
            "generic_rank_declared": self.generic_rank(),
            "base_field": f"Q({self.parameter})",
            "sections_verified_on_curve": len(points),
            "discriminant": str(E.discriminant()),
        }

    def validate_generic_rank_claim(self):
        """Recheck a one-section lower-bound certificate by specialization.

        An exact infinite-order rational point on one smooth specialization
        proves that the corresponding function-field section is non-torsion.
        This deliberately certifies only rank lower bound one; higher bounds
        require separate independence certificates.
        """
        certificate = self.data.get("rank_lower_certificate")
        if not isinstance(certificate, dict):
            raise ValueError("formula family has no rank_lower_certificate")
        from sage.all import Infinity, QQ
        if int(self._generic_rank or 0) < 1:
            raise ValueError("rank lower-bound certificate requires generic_rank >= 1")
        sections = self.data.get("sections") or []
        index = int(certificate.get("section_index", 0))
        if index < 0 or index >= len(sections):
            raise ValueError("rank certificate references an unavailable section")
        parameter_value = QQ(self._eval(certificate.get("specialization"), QQ(0)))
        E = self.curve(parameter_value)
        if E is None:
            raise ValueError("rank certificate specialization is singular")
        points = self.generic_section_points(parameter_value)
        if len(points) <= index:
            raise ValueError("rank certificate section is unavailable")
        order = points[index].order()
        if order != Infinity:
            raise ValueError("rank certificate specialization is not infinite order")
        return {
            "verified": True,
            "lower_bound": 1,
            "method": "exact infinite-order specialization",
            "specialization": str(parameter_value),
            "section_index": index,
            "point_order": "infinite",
        }
