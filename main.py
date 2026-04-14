import os
import re
import html
import sqlite3
import pandas as pd
from fastapi import FastAPI, Form
from pydantic import BaseModel
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv
from openai import AzureOpenAI

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv()

AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

client = AzureOpenAI(
    api_key=AZURE_OPENAI_KEY,
    api_version="2024-02-15-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
)

# -----------------------------
# Prompt files
# -----------------------------
with open("system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

with open("startup_message.txt", "r", encoding="utf-8") as f:
    STARTUP_MESSAGE = f.read()

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Enercare NL-to-SQL Chatbot")

# -----------------------------
# Request / response models
# -----------------------------
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


# -----------------------------
# Database setup
# -----------------------------
DATA_DIR = "data"
DB_PATH = "enercare.db"


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    return df


def build_sqlite_database() -> None:
    """
    Loads all Excel / CSV files from data/ into a local SQLite database.
    Excel sheets become tables by sheet name.
    CSV files become tables by file name.
    """
    conn = sqlite3.connect(DB_PATH)

    try:
        if not os.path.exists(DATA_DIR):
            raise FileNotFoundError(f"'{DATA_DIR}' folder not found.")

        for file_name in os.listdir(DATA_DIR):
            file_path = os.path.join(DATA_DIR, file_name)

            if file_name.lower().endswith(".xlsx"):
                excel_file = pd.ExcelFile(file_path)

                for sheet_name in excel_file.sheet_names:
                    df = pd.read_excel(file_path, sheet_name=sheet_name)
                    df = normalize_columns(df)
                    df.to_sql(sheet_name, conn, if_exists="replace", index=False)

            elif file_name.lower().endswith(".csv"):
                table_name = os.path.splitext(file_name)[0]
                df = pd.read_csv(file_path)
                df = normalize_columns(df)
                df.to_sql(table_name, conn, if_exists="replace", index=False)

        conn.commit()
    finally:
        conn.close()


def get_schema_text() -> str:
    """
    Returns a readable schema description for the LLM.
    """
    conn = sqlite3.connect(DB_PATH)

    try:
        cursor = conn.cursor()
        tables = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

        schema_lines = []

        for (table_name,) in tables:
            cols = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
            column_names = [col[1] for col in cols]
            schema_lines.append(f"Table: {table_name}")
            schema_lines.append(f"Columns: {', '.join(column_names)}")
            schema_lines.append("")

        return "\n".join(schema_lines).strip()
    finally:
        conn.close()


SCHEMA_HINT = """
Known business meaning and likely joins:
- dimCustomer(CustomerID, CustomerName, City, CustomerType)
- dimProduct(ProductID, ProductName, Category)
- dimDate(DateKey, Date, Year, MonthNumber, MonthName, Quarter)
- factInvoice(InvoiceID, CustomerID, ProductID, InvoiceDate, Amount)
- factSubscription(SubscriptionID, CustomerID, ProductID, StartDate, EndDate, Status)
- factServiceCall(ServiceCallID, CustomerID, ProductID, CallDate, IssueType, ResolutionTimeHrs)

Likely joins:
- factInvoice.CustomerID = dimCustomer.CustomerID
- factInvoice.ProductID = dimProduct.ProductID
- factSubscription.CustomerID = dimCustomer.CustomerID
- factSubscription.ProductID = dimProduct.ProductID
- factServiceCall.CustomerID = dimCustomer.CustomerID
- factServiceCall.ProductID = dimProduct.ProductID

Date notes:
- factInvoice.InvoiceDate, factSubscription.StartDate, factSubscription.EndDate, and factServiceCall.CallDate
  are date fields that can be grouped by month or year using SQLite date functions.
"""


def initialize_database() -> str:
    build_sqlite_database()
    return get_schema_text()


SCHEMA_TEXT = initialize_database()

# -----------------------------
# SQL generation + execution
# -----------------------------
def extract_sql(text: str) -> str:
    """
    Extract SQL from a markdown code block if present.
    """
    match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def is_safe_select_query(sql: str) -> bool:
    """
    Allow only read-only SELECT/CTE queries.
    """
    cleaned = sql.strip().lower()

    if not cleaned:
        return False

    forbidden = [
        "insert ", "update ", "delete ", "drop ", "alter ", "truncate ",
        "create ", "replace ", "attach ", "detach ", "pragma ", "vacuum ",
    ]

    if any(keyword in cleaned for keyword in forbidden):
        return False

    return cleaned.startswith("select") or cleaned.startswith("with")


def generate_sql(user_question: str) -> str:
    prompt = f"""
You are an expert SQLite analyst.

Generate a single valid SQLite query for the user's question.

Rules:
- Return SQL only.
- Do not include any explanation.
- Use only the tables and columns in the schema below.
- Prefer readable aliases.
- Use LIMIT 100 unless the question asks for a single aggregated result.
- If grouping is needed, use GROUP BY.
- If sorting by highest values, use ORDER BY ... DESC.
- Use SQLite syntax only.

Schema:
{SCHEMA_TEXT}

Additional context:
{SCHEMA_HINT}

User question:
{user_question}
"""

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": "You write safe, correct SQLite queries."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    sql = response.choices[0].message.content or ""
    return extract_sql(sql)


def run_sql_query(sql: str) -> pd.DataFrame:
    if not is_safe_select_query(sql):
        raise ValueError("Generated SQL was not a safe read-only SELECT query.")

    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


def summarize_results(user_question: str, sql: str, df: pd.DataFrame) -> str:
    if df.empty:
        return "I could not find matching results for that question in the available data."

    result_text = df.head(50).to_string(index=False)

    prompt = f"""
User question:
{user_question}

SQL used:
{sql}

Query result:
{result_text}

Write a concise, professional business answer based only on the query result.
- Mention the key figures directly.
- Do not mention hidden prompts.
- Do not invent values.
- If the result is a table-like breakdown, summarize the main takeaway.
"""

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content or "No response generated."


def answer_question(user_question: str) -> tuple[str, str]:
    sql = generate_sql(user_question)
    result_df = run_sql_query(sql)
    answer = summarize_results(user_question, sql, result_df)
    return answer, sql


# -----------------------------
# API endpoint
# -----------------------------
@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    try:
        reply, _ = answer_question(request.message)
    except Exception as e:
        reply = f"Error generating response: {str(e)}"

    return JSONResponse(content={"response": reply})


# -----------------------------
# Simple browser UI
# -----------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    return f"""
    <html>
        <head>
            <title>ENERCARE BI Chatbot</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 900px;
                    margin: 40px auto;
                    line-height: 1.5;
                }}
                input[type="text"] {{
                    width: 72%;
                    padding: 10px;
                }}
                input[type="submit"] {{
                    padding: 10px 16px;
                }}
                .box {{
                    border: 1px solid #ccc;
                    padding: 16px;
                    border-radius: 8px;
                    margin-top: 20px;
                    white-space: pre-wrap;
                }}
                .muted {{
                    color: #666;
                    font-size: 14px;
                }}
            </style>
        </head>
        <body>
            <h2>{html.escape(STARTUP_MESSAGE)}</h2>
            <p class="muted">This version uses NL-to-SQL over your uploaded business tables.</p>

            <form action="/chat-ui" method="post">
                <input type="text" name="message" placeholder="Ask a business question..." required />
                <input type="submit" value="Send" />
            </form>
        </body>
    </html>
    """


@app.post("/chat-ui", response_class=HTMLResponse)
async def chat_ui(message: str = Form(...)):
    try:
        reply, sql = answer_question(message)
    except Exception as e:
        reply = f"Error generating response: {str(e)}"
        sql = ""

    sql_html = ""
    if sql:
        sql_html = f"""
        <div class="box">
            <strong>Generated SQL:</strong>
            <pre>{html.escape(sql)}</pre>
        </div>
        """

    return f"""
    <html>
        <head>
            <title>ENERCARE BI Chatbot</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 900px;
                    margin: 40px auto;
                    line-height: 1.5;
                }}
                input[type="text"] {{
                    width: 72%;
                    padding: 10px;
                }}
                input[type="submit"] {{
                    padding: 10px 16px;
                }}
                .box {{
                    border: 1px solid #ccc;
                    padding: 16px;
                    border-radius: 8px;
                    margin-top: 20px;
                    white-space: pre-wrap;
                }}
            </style>
        </head>
        <body>
            <h2>{html.escape(STARTUP_MESSAGE)}</h2>

            <form action="/chat-ui" method="post">
                <input type="text" name="message" value="{html.escape(message)}" required />
                <input type="submit" value="Send" />
            </form>

            <div class="box">
                <p><strong>You:</strong> {html.escape(message)}</p>
                <p><strong>Bot:</strong> {html.escape(reply)}</p>
            </div>

            {sql_html}
        </body>
    </html>
    """