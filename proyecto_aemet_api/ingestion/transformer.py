"""Limpieza y transformación de datos crudos de AEMET."""
import re

import numpy as np
import pandas as pd


class DataTransformer:
    COLUMNAS_FLOAT = [
        "tmed", "prec", "tmin", "tmax", "hrMedia", "pintMax",
        "velmedia", "racha", "presMax", "presMin", "sol",
    ]

    COLUMNAS_HORA = [
        "horatmin", "horatmax", "horaHrMax", "horaHrMin",
        "horaracha", "horaPresMax", "horaPresMin", "horaPIntMax",
    ]

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # Fecha a datetime
        df["fecha"] = pd.to_datetime(df["fecha"], format="%Y-%m-%d", errors="coerce")

        # Categóricas / texto
        for col in ["indicativo", "provincia", "nombre"]:
            if col in df.columns:
                df[col] = df[col].astype("category")

        columnas_a_revisar = [
            c for c in self.COLUMNAS_FLOAT + self.COLUMNAS_HORA if c in df.columns
        ]

        # Detectar Acum / Varias / Ip
        mask_acum = pd.Series(False, index=df.index)
        mask_varias = pd.Series(False, index=df.index)
        mask_ip = pd.Series(False, index=df.index)

        for col in columnas_a_revisar:
            serie = df[col].apply(
                lambda x: str(x).strip().lower() if pd.notna(x) else np.nan
            )
            mask_acum |= (serie == "acum")
            mask_varias |= (serie == "varias")
            mask_ip |= (serie == "ip")

        df["precAcum"] = mask_acum.fillna(False)
        df["variasHoras"] = mask_varias.fillna(False)
        df["precIp"] = mask_ip.fillna(False)

        # Limpieza numérica
        for col in self.COLUMNAS_FLOAT:
            if col in df.columns:
                df[col] = df[col].apply(self._limpiar_numero)
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Numéricos -> float
        for col in ["altitud", "hrMax", "hrMin", "dir"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

        # Columnas de hora -> normalizadas a HH:MM
        for col in self.COLUMNAS_HORA:
            if col in df.columns:
                df[col] = df[col].apply(self._normalizar_hora)

        return df

    @staticmethod
    def _normalizar_hora(valor):
        if pd.isna(valor):
            return np.nan
        valor = str(valor).strip()
        if valor in ("<NA>", "nan", "NaN", "None", ""):
            return np.nan
        if re.fullmatch(r"\d{1,2}", valor):
            return f"{int(valor):02d}:00"
        return valor

    @staticmethod
    def _limpiar_numero(valor):
        if pd.isna(valor):
            return np.nan
        valor = str(valor).strip()
        if valor.lower() in ("ip",):
            return "0.05"
        if valor.lower() in ("acum", "varias", "<na>", "nan", "none", ""):
            return np.nan
        return valor.replace(",", ".")
