"""Complemento de ejemplo: el visor (F3) muestra los JSON ordenados y con sangría."""
import json

NOMBRE = "Visor de JSON"
DESCRIPCION = "Al ver un archivo .json con F3 lo muestra formateado y con sangría."
NAMES = {"es": NOMBRE, "en": "JSON viewer"}
DESCRIPTIONS = {"es": DESCRIPCION,
                "en": "Shows .json files formatted and indented when viewed with F3."}


def register(api):
    def view(name, data):
        try:
            obj = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            return None  # no es un JSON válido: se usa el visor normal
        return json.dumps(obj, indent=2, ensure_ascii=False)

    api.add_viewer(["json"], view)
