from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
import pandas as pd
import io
import google.generativeai as genai
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from utils import get_smart_defaults

# --- FastAPI App Initialization ---
app = FastAPI(title="LCA Simulation and Report API")
genai.configure(api_key="YOUR_GEMINI_API_KEY_HERE")

# Configure CORS (Crucial for frontend communication)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models (Corrected for Row-Oriented Input) ---

class LCASingleRowData(BaseModel):
    material_name: Optional[str] = None
    weight_kg: Optional[float] = None
    recycled_content: Optional[float] = None
    energy_extraction: Optional[float] = None
    energy_manufacturing: Optional[float] = None
    transport_km: Optional[float] = None
    transport_mode: Optional[str] = None
    recycling_yield: Optional[float] = None
    co2_extraction: Optional[float] = None
    co2_manufacturing: Optional[float] = None
    material_cost: Optional[float] = None
    transport_cost: Optional[float] = None
    eol_method: Optional[str] = None

class CustomDefaults(BaseModel):
    co2_per_kwh_extraction: Optional[float] = None
    co2_per_kwh_manufacturing: Optional[float] = None
    recycling_yield_default: Optional[float] = None
    transport_cost_per_km_default: Optional[float] = None
    avg_energy_extraction_mj: Optional[float] = None
    
class RequestPayload(BaseModel):
    project_metadata: Dict[str, Any] = Field(..., description="Project details")
    data: List[LCASingleRowData] = Field(..., description="List of LCA input rows")
    custom_defaults: Optional[CustomDefaults] = None

class ReportData(BaseModel):
    linear: Dict[str, Any]
    circular: Dict[str, Any]
    recommendations: List[Dict[str, str]]
    stage_impact: Dict[str, Any] # Stage impact data is passed for completeness

# --- Utility Functions ---

def get_default_imputation_values(custom_defaults: Optional[CustomDefaults]) -> Dict[str, float]:
    """Retrieves default values, prioritizing custom company parameters."""
    
    # System Defaults (Fallback if no custom_defaults are provided)
    defaults = {
        "co2_kwh_ext": 0.5,
        "co2_kwh_man": 0.35,
        "rec_yield": 90.0,
        "avg_energy_ext": 200.0,
        "transport_cost_km": 0.005
    }
    
    # Apply Custom Defaults if they exist
    if custom_defaults:
        defaults["co2_kwh_ext"] = custom_defaults.co2_per_kwh_extraction if custom_defaults.co2_per_kwh_extraction is not None else defaults["co2_kwh_ext"]
        defaults["co2_kwh_man"] = custom_defaults.co2_per_kwh_manufacturing if custom_defaults.co2_per_kwh_manufacturing is not None else defaults["co2_kwh_man"]
        defaults["rec_yield"] = custom_defaults.recycling_yield_default if custom_defaults.recycling_yield_default is not None else defaults["rec_yield"]
        defaults["avg_energy_ext"] = custom_defaults.avg_energy_extraction_mj if custom_defaults.avg_energy_extraction_mj is not None else defaults["avg_energy_ext"]
        defaults["transport_cost_km"] = custom_defaults.transport_cost_per_km_default if custom_defaults.transport_cost_per_km_default is not None else defaults["transport_cost_km"]

    return defaults


def fill_missing_values(df: pd.DataFrame, defaults: Dict[str, float]) -> pd.DataFrame:
    """
    Fills missing values using Smart Database lookup, custom factors, and mean imputation.
    """
    df = df.copy()
    
    # Rename columns to match snake_case
    df.columns = [col.lower() for col in df.columns]

    # Convert numeric columns to float
    for col in df.columns:
        if col not in ['transport_mode', 'eol_method', 'material_name']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # --- NEW Strategy: Smart Database Imputation (AI Logic) ---
    # This checks our JSON database for CO2 factors based on material name
    if 'co2_extraction' in df.columns:
        for index, row in df.iterrows():
            if pd.isnull(row['co2_extraction']):
                mat_name = row.get('material_name')
                # get_smart_defaults comes from your utils.py
                factor = get_smart_defaults(mat_name) if mat_name else defaults["co2_kwh_ext"]
                
                weight = row.get('weight_kg', 0.0)
                df.at[index, 'co2_extraction'] = weight * factor

    # --- Strategy 1: Factor-based Imputation (Manufacturing & Transport) ---
    df['energy_manufacturing'].fillna(defaults["avg_energy_ext"] * 0.05, inplace=True) 

    missing_co2_man = df['co2_manufacturing'].isnull()
    df.loc[missing_co2_man, 'co2_manufacturing'] = df.loc[missing_co2_man, 'energy_manufacturing'] * defaults["co2_kwh_man"]

    missing_transport_cost = df['transport_cost'].isnull()
    df.loc[missing_transport_cost, 'transport_cost'] = df.loc[missing_transport_cost, 'transport_km'] * defaults["transport_cost_km"]
    
    # --- Strategy 2: Simple Imputation (Mean/Mode as Last Resort) ---
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    for col in numeric_cols:
        if df[col].isnull().any():
            df[col].fillna(df[col].mean(), inplace=True)
    
    categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
    for col in categorical_cols:
        if df[col].isnull().any():
            df[col].fillna(df[col].mode()[0] if not df[col].mode().empty else 'unknown', inplace=True)
            
    return df


def run_linear_model(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Run linear (traditional) LCA model.
    FIX: Ensure all columns are explicitly cast to float before summation.
    """
    df_clean = df.fillna(0) 
    
    # CRITICAL FIX: Explicitly cast necessary columns to float/numeric just before calculation
    numeric_cols_for_calc = ['co2_extraction', 'co2_manufacturing', 'transport_km', 
                             'energy_extraction', 'energy_manufacturing', 'material_cost', 'transport_cost']
    
    for col in numeric_cols_for_calc:
        # Use defensive casting: convert to numeric, coercing errors (if any residual strings), and filling NaN with 0.0
        df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0.0)


    CO2_total = df_clean["co2_extraction"].sum() + df_clean["co2_manufacturing"].sum() + (df_clean["transport_km"] * 0.001).sum()
    Energy_total = df_clean["energy_extraction"].sum() + df_clean["energy_manufacturing"].sum()
    Cost_total = df_clean["material_cost"].sum() + df_clean["transport_cost"].sum()
    
    return {
        "CO2_total_kg": float(CO2_total),
        "Energy_total_MJ": float(Energy_total),
        "Cost_total_USD": float(Cost_total),
        "Circularity": {"MCI": 0.0},
    }

def run_circular_model(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Run circular economy model with recycling benefits.
    FIX: Ensure all columns are explicitly cast to float before summation.
    """
    df_clean = df.fillna(0)
    
    # CRITICAL FIX: Explicitly cast necessary columns to float/numeric just before calculation
    numeric_cols_for_calc = ['recycled_content', 'co2_extraction', 'co2_manufacturing', 'transport_km', 
                             'energy_extraction', 'energy_manufacturing', 'material_cost', 'transport_cost', 'recycling_yield']
    
    for col in numeric_cols_for_calc:
        df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0.0)

    
    # Key input: Recycled Content Percentage (R)
    R = df_clean["recycled_content"].mean() / 100.0
    R_factor = 1.0 - (0.7 * R) # Circularity factor: 100% recycled gives 70% overall impact reduction on extraction/manufacturing
    
    # Calculate impacts based on recycled factor
    CO2_total = (df_clean["co2_extraction"] * R_factor).sum() + (df_clean["co2_manufacturing"] * R_factor).sum() + (df_clean["transport_km"] * 0.0005).sum() # Transport slightly lower
    Energy_total = (df_clean["energy_extraction"] * R_factor).sum() + (df_clean["energy_manufacturing"] * R_factor).sum()
    
    # Cost modeling: Assume recycling reduces material cost, increasing profit potential
    Cost_material = df_clean["material_cost"].sum() * (1 - 0.1 * R) # 10% material cost savings from recycling
    Cost_transport = df_clean["transport_cost"].sum()
    Cost_total = Cost_material + Cost_transport
    
    # Circularity Metric (Mock MCI calculation based on core inputs)
    MCI = R * (df_clean["recycling_yield"].mean() / 100.0)
    
    return {
        "CO2_total_kg": float(CO2_total),
        "Energy_total_MJ": float(Energy_total),
        "Cost_total_USD": float(Cost_total),
        "Circularity": {"MCI": float(min(MCI, 0.95))}, # Cap MCI slightly below 1.0
        "Virgin_Input_percent": float((1.0 - R) * 100),
        "Recycled_Input_percent": float(R * 100)
    }


def run_scenario_with_recommendation(df: pd.DataFrame, defaults: Dict[str, float]) -> Dict[str, Any]:
    """Run LCA scenarios and provide a recommendation."""
    
    # 1. Fill missing data using the prioritized defaults
    df_imputed = fill_missing_values(df, defaults)
    
    # 2. Run Models
    linear_results = run_linear_model(df_imputed)
    circular_results = run_circular_model(df_imputed)
    
    # 3. Determine Recommendation
    # Score based on a weighted average of CO2 and Cost
    linear_score = linear_results["CO2_total_kg"] * 0.5 + linear_results["Cost_total_USD"] * 0.5
    circular_score = circular_results["CO2_total_kg"] * 0.5 + circular_results["Cost_total_USD"] * 0.5

    if circular_score < linear_score:
        recommendation = "Circular Model is optimal: projected to save money and reduce emissions."
    else:
        # A negative cost savings scenario
        recommendation = "Linear Model is currently cheaper, but circularity is essential for long-term supply stability. Optimization needed to close the cost gap."

    results = {
        "linear": linear_results,
        "circular": circular_results,
        "recommendation": recommendation
    }
    return results


def generate_report(report_data: ReportData) -> io.BytesIO:
    """
    Generates a PDF report from simulation results.
    FIX: Implemented robust division checks and safe dict access to prevent crash.
    """
    buffer = io.BytesIO()
    
    # Use standard styles
    styles = getSampleStyleSheet()
    
    # Create custom ParagraphStyle for safety, although 'Normal' should exist
    if 'Normal' not in styles:
        styles['Normal'] = styles.get('BodyText', styles['BodyText'])
    if 'h2' not in styles:
        styles['h2'] = styles.get('Heading2', styles['Normal'])

    doc = SimpleDocTemplate(buffer, pagesize=letter)
    story = []

    # Title
    story.append(Paragraph("AI-POWERED CIRCULARITY LCA REPORT", styles['Title']))
    story.append(Spacer(1, 12))
    
    # --- Summary Table ---
    story.append(Paragraph("<b>Summary Impact Comparison</b>", styles['h2']))

    # CRITICAL FIX: Explicitly cast the data segments to dicts and use .get()
    # This prevents the 'int' object has no attribute 'get' error
    linear_data = report_data.linear if isinstance(report_data.linear, dict) else {}
    circular_data = report_data.circular if isinstance(report_data.circular, dict) else {}
    
    linear_co2 = linear_data.get('CO2_total_kg', 0.0)
    circular_co2 = circular_data.get('CO2_total_kg', 0.0)
    linear_cost = linear_data.get('Cost_total_USD', 0.0)
    circular_cost = circular_data.get('Cost_total_USD', 0.0)
    
    # Further check to ensure Circularity is a dictionary before using .get()
    linear_mci = linear_data.get('Circularity')
    linear_mci = linear_mci.get('MCI', 0.0) if isinstance(linear_mci, dict) else 0.0
    
    circular_mci = circular_data.get('Circularity')
    circular_mci = circular_mci.get('MCI', 0.0) if isinstance(circular_mci, dict) else 0.0

    # Calculate CO2 Reduction Percentage SAFELY
    co2_reduction_percent = 0.0
    if linear_co2 > 0:
        co2_reduction_percent = ((1 - (circular_co2 / linear_co2)) * 100)
    
    summary_data = [
        ['Metric', 'Linear Scenario', 'Circular Scenario', 'Savings/Improvement'],
        ['Total CO2 (kg)', f"{linear_co2:.2f}", f"{circular_co2:.2f}", 
         f"{co2_reduction_percent:.1f}%"],
        ['Total Cost (USD)', f"{linear_cost:.2f}", f"{circular_cost:.2f}", 
         f"${(linear_cost - circular_cost):.2f}"],
        ['Circularity Score (MCI)', f"{linear_mci:.2f}", f"{circular_mci:.2f}", 
         f"N/A"]
    ]
    summary_table = Table(summary_data, colWidths=[150, 100, 100, 150])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#104050')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#2d3748')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#a0aec0')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#a0aec0')),
        ('TEXTCOLOR', (1, 1), (1, -1), colors.white),
        ('TEXTCOLOR', (2, 1), (2, -1), colors.HexColor('#4ade80')),
        ('TEXTCOLOR', (3, 1), (3, -1), colors.HexColor('#4ade80')),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold')
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 24))

    # Recommendations section
    story.append(Paragraph("<b>AI-Driven Recommendations</b>", styles['h2']))
    for rec in report_data.recommendations:
        # Check if title and text fields exist before formatting
        title_text = rec.get('title', 'Recommendation')
        body_text = rec.get('text', 'No description available.')
        story.append(Paragraph(f"• <b>{title_text}:</b> {body_text}", styles['Normal']))
        story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)
    return buffer


# --- API Endpoints ---

@app.post("/impute")
async def impute_data(payload: RequestPayload) -> Dict[str, Any]:
    """Endpoint for AI data imputation (filling missing values)."""
    
    # 1. Get defaults (company-specific or system)
    defaults = get_default_imputation_values(payload.custom_defaults)
    
    # 2. Convert incoming rows to DataFrame
    df = pd.DataFrame([row.dict() for row in payload.data])
    
    # 3. Fill missing values using the prioritized logic
    imputed_df = fill_missing_values(df, defaults)

    # 4. Convert DataFrame back to row-oriented list for JSON response
    imputed_data = imputed_df.to_dict('records')

    return {"imputed_data": imputed_data}


@app.post("/simulate")
async def simulate_project(payload: RequestPayload) -> Dict[str, Any]:
    """Endpoint to run the full LCA simulation."""
    
    # 1. Get defaults (company-specific or system)
    defaults = get_default_imputation_values(payload.custom_defaults)
    
    # 2. Convert incoming rows to DataFrame
    df = pd.DataFrame([row.dict() for row in payload.data])
    
    # 3. Run the scenarios
    results = run_scenario_with_recommendation(df, defaults)
    
    # The frontend expects a separate field for Virgin/Recycled percentages 
    # which are calculated inside run_circular_model
    if 'circular' in results:
        # Re-run circular model logic just to grab the percentage calculation
        circular_results_full = run_circular_model(df) 
        results['circular']['Virgin_Input_percent'] = circular_results_full.get('Virgin_Input_percent', 40)
        results['circular']['Recycled_Input_percent'] = circular_results_full.get('Recycled_Input_percent', 60)
        
    return {"results": results}


@app.post("/report")
async def generate_report_endpoint(report_data: ReportData) -> Response:
    """Endpoint to generate and return a PDF report."""
    pdf_buffer = generate_report(report_data)
    
    # The frontend is expecting a blob response with PDF content type
    return Response(content=pdf_buffer.getvalue(), media_type="application/pdf", headers={
        "Content-Disposition": "attachment; filename=lca_report.pdf"
    })
