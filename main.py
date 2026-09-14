from fastapi import FastAPI, HTTPException
import pandapower as pp
import pandapower.shortcircuit as sc
from pydantic import BaseModel
import os
import uvicorn

app = FastAPI(
    title="Motor Integral de Análisis Eléctrico - 50 Buses ACSR 4/0",
    version="3.0.0"
)

class AnalisisParams(BaseModel):
    temperatura_ambiente: float = 30.0  # °C
    factor_proyeccion: float = 1.0      # Factor de crecimiento de demanda
    indice_bus_falla: int = 15          # Nodo seleccionado para estudio de cortocircuito (coincide con Apps Script)

def construir_red_50_buses(temp: float, factor_carga: float):
    net = pp.create_empty_network(f_hz=50.0)
    
    # Parámetros del conductor ACSR 4/0
    r_20 = 0.548           # Ohm/km a 20°C
    alpha = 0.00403        # Coeficiente térmico del aluminio
    r_ajustada = r_20 * (1 + alpha * (temp - 20.0))  # Ajuste térmico
    x_km = 0.410           # Reactancia inductiva Ohm/km
    c_km = 9.5             # Capacitancia nF/km
    max_i_ka = 0.260       # Capacidad máxima admisible (260 A)

    # 1. Creación de 50 barras en 24.9 kV
    buses = [pp.create_bus(net, vn_kv=24.9, name=f"Bus_{i+1}") for i in range(50)]

    # 2. Generador / Subestación Principal (Slack) en la Barra 0
    pp.create_ext_grid(net, bus=buses[0], vm_pu=1.0, mva_base=100.0, name="Subestación Principal ENDE")

    # 3. Topología de líneas ACSR 4/0
    for i in range(49):
        pp.create_line_from_parameters(
            net, from_bus=buses[i], to_bus=buses[i+1], length_km=3.2,
            r_ohm_per_km=r_ajustada, x_ohm_per_km=x_km, c_nf_per_km=c_km,
            max_i_ka=max_i_ka, name=f"Linea_{i+1}_{i+2}"
        )
    # Cierre de anillo
    pp.create_line_from_parameters(
        net, from_bus=buses[49], to_bus=buses[5], length_km=5.0,
        r_ohm_per_km=r_ajustada, x_ohm_per_km=x_km, c_nf_per_km=c_km,
        max_i_ka=max_i_ka, name="Linea_Anillo_Cierre"
    )

    # 4. Cargas distribuidas con factor de proyección
    for i in range(1, 50):
        pp.create_load(net, bus=buses[i], p_mw=0.12 * factor_carga, q_mvar=0.04 * factor_carga, name=f"Carga_{i+1}")

    return net

@app.post("/api/analisis/completo")
def ejecutar_analisis_sistema(params: AnalisisParams):
    net = construir_red_50_buses(params.temperatura_ambiente, params.factor_proyeccion)
    
    # --- 1. FLUJO DE CARGA Y PÉRDIDAS ---
    try:
        pp.runpp(net, algorithm='nr')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fallo de convergencia en Flujo de Carga: {str(e)}")

    perdidas_kw = float(net.res_line["pl_mw"].sum() * 1000)
    min_voltaje_pu = float(net.res_bus["vm_pu"].min())
    max_cargabilidad_pct = float(net.res_line["loading_percent"].max())

    # --- 2. CORTOCIRCUITOS (3F, 2F, 1FN - IEC 60909) ---
    net.ext_grid["s_sc_max_mva"] = 300.0
    net.ext_grid["rx_max"] = 0.1
    net.ext_grid["s_sc_min_mva"] = 200.0
    net.ext_grid["rx_min"] = 0.1

    resultados_sc = {}
    target_bus = params.indice_bus_falla

    tipos_falla = {"3ph": "Trifásica", "2ph": "Bifásica", "1ph": "Monofásica a Tierra (1FN)"}
    for codigo, nombre in tipos_falla.items():
        try:
            sc.calc_sc(net, fault=codigo, case="max")
            ikss = float(net.res_bus_sc.at[target_bus, "ikss_ka"]) if "ikss_ka" in net.res_bus_sc.columns else 0.0
            resultados_sc[nombre] = f"{round(ikss, 3)} kA"
        except Exception as ex:
            resultados_sc[nombre] = f"No disponible: {str(ex)}"

    # --- 3. ESTABILIDAD Y CONTINGENCIAS N-1 ---
    contingencias_criticas = 0
    for line_id in net.line.index:
        net_n1 = construir_red_50_buses(params.temperatura_ambiente, params.factor_proyeccion)
        net_n1.line.loc[line_id, "in_service"] = False
        try:
            pp.runpp(net_n1, max_iteration=40)
            if net_n1.res_bus["vm_pu"].min() < 0.90 or net_n1.res_line["loading_percent"].max() > 100.0:
                contingencias_criticas += 1
        except:
            contingencias_criticas += 1

    r_20 = 0.548
    alpha = 0.00403
    r_efectiva = r_20 * (1 + alpha * (params.temperatura_ambiente - 20.0))

    return {
        "estado": "Análisis completado con éxito",
        "datos_operativos_entrada": {
            "temperatura_ambiente_c": params.temperatura_ambiente,
            "resistencia_acsr_40_ohm_km": round(r_efectiva, 4),
            "factor_proyeccion_carga": params.factor_proyeccion,
            "bus_analisis_cortocircuito": target_bus
        },
        "resultados_flujo_y_perdidas": {
            "perdidas_tecnicas_totales_kw": round(perdidas_kw, 2),
            "voltaje_minimo_red_pu": round(min_voltaje_pu, 4),
            "maxima_cargabilidad_linea_pct": round(max_cargabilidad_pct, 2)
        },
        "cortocircuitos_iec60909": resultados_sc,
        "estabilidad_contingencias_n1": {
            "total_lineas_evaluadas": len(net.line),
            "contingencias_con_violacion_o_colapso": contingencias_criticas
        }
    }

@app.get("/")
def leer_raiz():
    return {
        "sistema": "Motor Integral de Análisis Eléctrico - ENDE Delbeni",
        "estado": "Activo",
        "documentacion": "/docs"
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
