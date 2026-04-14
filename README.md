# Enercare-BI-ChatBot
A Natural Language to SQL BI chatbot that enables users to ask business questions over Excel/CSV data. Uses Azure OpenAI for SQL generation, SQLite for query execution, and FastAPI for API/UI.

An end-to-end **Natural Language to SQL (NL-to-SQL) BI chatbot** that enables users to query Excel/CSV data using conversational inputs.

This project transforms structured data into a **conversational analytics experience**, allowing users to ask business questions and receive accurate, data-driven insights.

---

## Features

- Convert natural language → SQL queries using Azure OpenAI  
- Execute queries on structured data using SQLite  
- Support multiple Excel and CSV files  
- Generate business-friendly insights from query results  
- Built with FastAPI (API + simple web UI)  
- Safe SQL execution with read-only validation  


---

## Setup Instructions

1. Clone the repository

```bash
git clone https://github.com/your-username/enercare-bi-chatbot.git
cd enercare-bi-chatbot

2. Create a virtual environment
python -m venv venv
.\venv\Scripts\activate

3. Install dependencies
pip install -r requirements.txt

4. Configure environment variables
AZURE_OPENAI_KEY=your_key_here
AZURE_OPENAI_ENDPOINT=your_endpoint_here
AZURE_OPENAI_DEPLOYMENT=your_deployment_name

5. Add data
Place your Excel/CSV files inside the data/ folder.

6. Run the application
uvicorn main:app --reload

7. Open in browser
http://127.0.0.1:8000/

## Sample Questions
How many customers do we have in each city?
What is total invoice amount by city?
Which product category has the highest invoice amount?
How many active subscriptions do we have?
How many service calls were logged by issue type?
What is average resolution time by product?
