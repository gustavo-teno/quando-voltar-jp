"""
Etapa 1 — coleta de dados de tabuademares.com/br/paraiba/joao-pessoa.

Objetivo: gravar em data/raw/ os CSVs que as Etapas 2 e 3 vão consumir.

    mares_<ano>.csv      tábua de marés do ano (4 marés por dia)
    mares_previsao.csv   marés do mês corrente, para cruzar com a previsão
    ondas.csv            altura de onda hora a hora (~7 dias à frente)
    vento.csv            velocidade do vento hora a hora (~7 dias à frente)

As funções abaixo estão vazias de propósito. Abra o site no navegador, use o
DevTools para descobrir onde cada dado vive no HTML e implemente o parse.
As colunas de cada CSV são sua decisão — só precisam sustentar as etapas
seguintes (ver README).

Rodar com: uv run python src/scrape.py
"""

import time
from datetime import datetime
from pathlib import Path
import re
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup

# Os imports acima são o ponto de partida: você vai usar todos eles.
# Seu editor pode marcá-los como não usados até você preencher as funções.

URL_BASE = "https://tabuademares.com/br/paraiba/joao-pessoa"
URL_ONDAS = f"{URL_BASE}/previsao/ondas"
URL_VENTO = f"{URL_BASE}/previsao/vento"

# Servidores rejeitam clientes sem User-Agent. Identifique-se.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Segundos de pausa entre requests. Não tire isso
PAUSA = 1.5

DIR_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def baixar_html(url: str, dados_post: dict | None = None) -> str:
    """Baixa uma pagina e devolve o HTML.

    GET por padrao; POST quando `dados_post` vier preenchido.
    """
    if dados_post:
        resp = requests.post(url, data=dados_post, headers=HEADERS, timeout=30)
    else:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    resp.encoding = "utf-8"
    print(f"{resp.request.method} {url} -> {resp.status_code}, {len(resp.text)} caracteres")
    return resp.text

def parsear_mares(html: str, ano: int, mes: int) -> list[dict]:
    """Extrai da tábua mensal uma linha por evento de maré.

    Cada dia tem cerca de 4 eventos (duas altas, duas baixas). Além de
    horário e altura, a página traz informação de coeficiente de maré e de
    fase da lua — decida o que vale a pena capturar.

    A tabela de desktop e a tabela "swipe" contêm a mesma informação. Usamos
    apenas a primeira para não duplicar os eventos.
    """
    sopa = BeautifulSoup(html, "html.parser")
    tabela = sopa.find("table", id="tabla_mareas")
    if tabela is None:
        raise ValueError("Tabela de marés não encontrada no HTML.")

    eventos = []
    for linha in tabela.select("tr.tabla_mareas_fila"):
        dia = linha.select_one("td.tabla_mareas_dia .tabla_mareas_dia_numero")
        if dia is None:
            continue

        coeficiente = linha.select_one(".tabla_mareas_coeficiente_numero")
        classe = linha.select_one(".tabla_mareas_coeficiente_altura")
        classe_texto = classe.get_text(strip=True) if classe else None
        if classe_texto:
            
            classe_texto = unicodedata.normalize("NFKD", classe_texto).encode(
                "ascii", "ignore"
            ).decode()

            if classe_texto == "mdio":
                classe_texto = "medio"
        for celula in linha.select("td.tabla_mareas_marea"):
            horario = celula.select_one(".tabla_mareas_marea_hora")
            altura = celula.select_one(".tabla_mareas_marea_altura_numero")
            if horario is None or altura is None:
                continue

            eventos.append(
                {
                    "data": f"{ano:04d}-{mes:02d}-{int(dia.get_text(strip=True)):02d}",
                    "hora": horario.get_text(" ", strip=True).zfill(5),
                    "altura_m": float(altura.get_text(strip=True).replace(",", ".")),
                    "tipo": (
                        "alta"
                        if celula.select_one(".tabla_mareas_marea_pleamar")
                        else "baixa"
                    ),
                    "coeficiente": (
                        int(re.search(r"\d+", coeficiente.get_text(" ", strip=True)).group())
                        if coeficiente
                        else None
                    ),
                    "classe_coeficiente": classe_texto,
                }
            )
    return eventos


def parsear_previsao(html: str, ano: int) -> list[dict]:
    """Extrai leituras horárias das páginas de previsão de onda e de vento.

    As duas páginas têm o mesmo layout: um bloco por dia, com uma linha por
    hora dentro. Uma função só deve dar conta das duas.

    O ano recebido deve ser o ano do primeiro dia exibido. A virada de ano é
    inferida quando os blocos passam, por exemplo, de dezembro para janeiro.
    """
    meses = {
        "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
        "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
    }
    sopa = BeautifulSoup(html, "html.parser")
    leituras = []
    mes_anterior = None
    ano_atual = ano

    for ficha in sopa.select(".ficha"):
        dia = ficha.select_one(".f_fecha .dia")
        mes = ficha.select_one(".f_fecha .mes")
        if dia is None or mes is None:
            continue
        numero_mes = meses.get(mes.get_text(strip=True).upper())
        if numero_mes is None:
            raise ValueError(f"Mês de previsão desconhecido: {mes.get_text(strip=True)!r}")
        if mes_anterior is not None and numero_mes < mes_anterior:
            ano_atual += 1
        mes_anterior = numero_mes
        data = f"{ano_atual:04d}-{numero_mes:02d}-{int(dia.get_text(strip=True)):02d}"

        for linha in ficha.select(".f_temp_horas"):
            campos = linha.select(".f_temp_hora")
            valor = linha.select_one(
                ".tabla_mareas_marea_altura_numero, .grafico_temp_barra_relleno"
            )
            if len(campos) < 2 or valor is None:
                continue
            numero = re.search(r"[-+]?\d+(?:[,.]\d+)?", valor.get_text(" ", strip=True))
            if numero is None:
                continue
            leituras.append(
                {
                    "data": data,
                    "hora": campos[0].get_text(strip=True).zfill(5),
                    "valor": float(numero.group().replace(",", ".")),
                    "direcao": campos[1].get_text(strip=True),
                }
            )
    return leituras


def coletar_mares_do_ano(ano: int) -> pd.DataFrame:
    """Junta os 12 meses da tábua de marés de `ano` num DataFrame.

    Os meses são obtidos pelo formulário da própria página, cujo único campo é
    `fecha` no formato ISO.
    """
    eventos = []
    for mes in range(1, 13):
        html = baixar_html(URL_BASE, {"fecha": f"{ano:04d}-{mes:02d}-01"})
        eventos.extend(parsear_mares(html, ano, mes))
        if mes < 12:
            time.sleep(PAUSA)
    return pd.DataFrame(eventos, columns=[
        "data", "hora", "altura_m", "tipo", "coeficiente", "classe_coeficiente"
    ])


def main(ano: int = 2025) -> None:
    DIR_RAW.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now()

    def salvar(dados: pd.DataFrame, arquivo: Path) -> None:
        dados.to_csv(arquivo, index=False)
        print(f"{arquivo.name}: {len(dados)} linhas")

    mares_ano = coletar_mares_do_ano(ano)
    salvar(mares_ano, DIR_RAW / f"mares_{ano}.csv")
    time.sleep(PAUSA)
    html_mares = baixar_html(URL_BASE, {"fecha": hoje.strftime("%Y-%m-01")})
    mares_previsao = pd.DataFrame(
        parsear_mares(html_mares, hoje.year, hoje.month), columns=mares_ano.columns
    )
    salvar(mares_previsao, DIR_RAW / "mares_previsao.csv")

    time.sleep(PAUSA)
    ondas = pd.DataFrame(parsear_previsao(baixar_html(URL_ONDAS), hoje.year))
    salvar(ondas, DIR_RAW / "ondas.csv")

    time.sleep(PAUSA)
    vento = pd.DataFrame(parsear_previsao(baixar_html(URL_VENTO), hoje.year))
    salvar(vento, DIR_RAW / "vento.csv")


if __name__ == "__main__":
    main()
