import unittest
import sys
import types


def _install_fastapi_stubs():
    if "fastapi" in sys.modules and "fastapi.responses" in sys.modules:
        return

    fastapi_mod = types.ModuleType("fastapi")

    class DummyFastAPI:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

        def get(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    class DummyHTTPException(Exception):
        pass

    def dummy_param(*args, **kwargs):
        return None

    class DummyUploadFile:
        pass

    fastapi_mod.FastAPI = DummyFastAPI
    fastapi_mod.File = dummy_param
    fastapi_mod.Form = dummy_param
    fastapi_mod.UploadFile = DummyUploadFile
    fastapi_mod.HTTPException = DummyHTTPException

    responses_mod = types.ModuleType("fastapi.responses")

    class DummyJSONResponse(dict):
        def __init__(self, status_code=200, content=None):
            super().__init__(content or {})
            self.status_code = status_code

    responses_mod.JSONResponse = DummyJSONResponse

    sys.modules["fastapi"] = fastapi_mod
    sys.modules["fastapi.responses"] = responses_mod


_install_fastapi_stubs()
from app import parse_neoenergia_pe


class NeoenergiaParserTests(unittest.TestCase):
    def test_multa_juros_reference_invoice_and_amount(self):
        text = """
REF:MÊS/ANO                       TOTAL A PAGAR R$                         VENCIMENTO
12/2025                                        7,79                   26/01/2026
ITENS DA FATURA
Multa-NF 391026567 7,42
Juros-NF 391026567 0,37
TOTAL 7,79
"""
        result = parse_neoenergia_pe(text)

        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["description"], "Multa-NF")
        self.assertEqual(result["items"][0]["reference_invoice"], "391026567")
        self.assertAlmostEqual(result["items"][0]["amount"], 7.42, places=2)

        self.assertEqual(result["items"][1]["description"], "Juros-NF")
        self.assertEqual(result["items"][1]["reference_invoice"], "391026567")
        self.assertAlmostEqual(result["items"][1]["amount"], 0.37, places=2)

    def test_linha_digitavel_boleto_is_captured_and_normalized(self):
        text = """
34191.09768 98931.082931 85834.530009 3 13540000044413
"""
        result = parse_neoenergia_pe(text)
        self.assertEqual(
            result["barcode"]["linha_digitavel"],
            "34191097689893108293185834530009313540000044413",
        )

    def test_meter_reading_fallback_block(self):
        text = """
MEDIDOR B92417
LEITURA ANTERIOR 18/12/2025
LEITURA ATUAL 16/01/2026
PRÓXIMA LEITURA 13/02/2026
"""
        result = parse_neoenergia_pe(text)

        self.assertEqual(len(result["meter_readings"]), 1)
        reading = result["meter_readings"][0]
        self.assertEqual(reading["meter"], "B92417")
        self.assertEqual(reading["previous_reading_date"], "2025-12-18")
        self.assertEqual(reading["current_reading_date"], "2026-01-16")
        self.assertEqual(reading["next_reading_date"], "2026-02-13")

    def test_warns_when_items_sum_diverges_from_total(self):
        text = """
REF:MÊS/ANO                       TOTAL A PAGAR R$                         VENCIMENTO
12/2025                                        100,00                   26/01/2026
ITENS DA FATURA
Multa-NF 391026567 7,42
Juros-NF 391026567 0,37
TOTAL 100,00
"""
        result = parse_neoenergia_pe(text)
        warnings = result["validation"]["warnings"]
        self.assertTrue(any(w.startswith("items_total_mismatch:") for w in warnings))

    def test_moves_tax_lines_to_taxes_and_keeps_chargeable_items(self):
        text = """
REF:MÊS/ANO                       TOTAL A PAGAR R$                         VENCIMENTO
01/2026                                      444,13                   26/01/2026
ITENS DA FATURA
TUSD GDII com trib.              436,34 ICMS 20,50 89,44
Multa-NF 391026567 7,42
Juros-NF 391026567 0,37
TOTAL 444,13
PIS 346,89 1,14 3,95
COFINS 346,89 5,23 18,14
ICMS 436,34 20,50 89,44
"""
        result = parse_neoenergia_pe(text)

        self.assertEqual(result["validation"]["items_total"], 444.13)
        warnings = result["validation"]["warnings"]
        self.assertFalse(any(w.startswith("items_total_mismatch:") for w in warnings))

        item_descriptions = [item["description"] for item in result["items"]]
        self.assertIn("TUSD GDII com trib.", item_descriptions)
        self.assertIn("Multa-NF", item_descriptions)
        self.assertIn("Juros-NF", item_descriptions)
        self.assertNotIn("PIS", item_descriptions)
        self.assertNotIn("COFINS", item_descriptions)
        self.assertNotIn("ICMS", item_descriptions)

        tusd_item = next(item for item in result["items"] if item["description"] == "TUSD GDII com trib.")
        self.assertAlmostEqual(tusd_item["amount"], 436.34, places=2)

        taxes = result["taxes"]
        self.assertEqual(len(taxes), 3)
        pis = next(t for t in taxes if t["type"] == "PIS")
        cofins = next(t for t in taxes if t["type"] == "COFINS")
        icms = next(t for t in taxes if t["type"] == "ICMS")

        self.assertAlmostEqual(pis["base"], 346.89, places=2)
        self.assertAlmostEqual(pis["rate"], 1.14, places=2)
        self.assertAlmostEqual(pis["amount"], 3.95, places=2)
        self.assertAlmostEqual(cofins["amount"], 18.14, places=2)
        self.assertAlmostEqual(icms["amount"], 89.44, places=2)

    def test_extracts_meter_reading_row_with_dates(self):
        text = """
LEITURA ANTERIOR 18/12/2025
LEITURA ATUAL 16/01/2026
PRÓXIMA LEITURA 13/02/2026
B92417  Energia Ativa  Único  6.328,00  8.327,00  1,00000  0,00
"""
        result = parse_neoenergia_pe(text)

        self.assertEqual(len(result["meter_readings"]), 1)
        reading = result["meter_readings"][0]
        self.assertEqual(reading["meter"], "B92417")
        self.assertEqual(reading["measure"], "Energia Ativa")
        self.assertAlmostEqual(reading["previous_reading"], 6328.00, places=2)
        self.assertAlmostEqual(reading["current_reading"], 8327.00, places=2)
        self.assertAlmostEqual(reading["multiplier"], 1.0, places=5)
        self.assertAlmostEqual(reading["consumption_kwh"], 0.0, places=2)
        self.assertEqual(reading["previous_reading_date"], "2025-12-18")
        self.assertEqual(reading["current_reading_date"], "2026-01-16")
        self.assertEqual(reading["next_reading_date"], "2026-02-13")

    def test_tusd_uses_first_monetary_value_not_trailing_tax_values(self):
        text = """
REF:MÊS/ANO                       TOTAL A PAGAR R$                         VENCIMENTO
01/2026                                      444,13                   11/02/2026
ITENS DA FATURA
TUSD GDII com trib.                                                                                436,34                  22,09            436,34      20,50
Multa-NF 391026567 7,42
Juros-NF 391026567 0,37
TOTAL 444,13
PIS 346,89 1,14 3,95
COFINS 346,89 5,23 18,14
ICMS 436,34 20,50 89,44
"""
        result = parse_neoenergia_pe(text)
        tusd_item = next(item for item in result["items"] if item["description"] == "TUSD GDII com trib.")
        self.assertAlmostEqual(tusd_item["amount"], 436.34, places=2)
        self.assertEqual(result["validation"]["items_total"], 444.13)
        self.assertNotIn("items_total_mismatch:0.01", result["validation"]["warnings"])
        self.assertFalse(any(w.startswith("items_total_mismatch:") for w in result["validation"]["warnings"]))

    def test_extracts_meter_row_from_noisy_layout_and_clears_warning(self):
        text = """
REF:MÊS/ANO                       TOTAL A PAGAR R$                         VENCIMENTO
01/2026                                      444,13                   11/02/2026
ITENS DA FATURA
TUSD GDII com trib. 436,34
Multa-NF 391026567 7,42
Juros-NF 391026567 0,37
TOTAL 444,13
LEITURA ANTERIOR 18/12/2025
LEITURA ATUAL 16/01/2026
PRÓXIMA LEITURA 13/02/2026
B92417 ... 6.328,00 ... 8.327,00 ... 1,00000 ... 0,00
"""
        result = parse_neoenergia_pe(text)

        self.assertEqual(len(result["meter_readings"]), 1)
        reading = result["meter_readings"][0]
        self.assertEqual(reading["meter"], "B92417")
        self.assertEqual(reading["measure"], "Energia Ativa")
        self.assertAlmostEqual(reading["previous_reading"], 6328.00, places=2)
        self.assertAlmostEqual(reading["current_reading"], 8327.00, places=2)
        self.assertAlmostEqual(reading["multiplier"], 1.0, places=5)
        self.assertAlmostEqual(reading["consumption_kwh"], 0.0, places=2)
        self.assertEqual(reading["previous_reading_date"], "2025-12-18")
        self.assertEqual(reading["current_reading_date"], "2026-01-16")
        self.assertEqual(reading["next_reading_date"], "2026-02-13")
        self.assertNotIn("meter_readings_not_found", result["validation"]["warnings"])


if __name__ == "__main__":
    unittest.main()
