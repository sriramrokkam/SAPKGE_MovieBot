import os
import json
import pandas as pd
import io
from flask import Flask, request, render_template
from hdbcli import dbapi
from gen_ai_hub.proxy.native.openai import chat

from dotenv import load_dotenv

app = Flask(__name__)

# Load environment variables from .env file
load_dotenv()

# SAP HANA connection variables
HANA_ADDRESS = os.getenv("HANA_ADDRESS")
HANA_PORT = int(os.getenv("HANA_PORT", 443))
HANA_USER = os.getenv("HANA_USER")
HANA_PASSWORD = os.getenv("HANA_PASSWORD")
HANA_ENCRYPT = os.getenv("HANA_ENCRYPT", "True").lower() == "true"
HANA_SSL_VALIDATE_CERTIFICATE = os.getenv("HANA_SSL_VALIDATE_CERTIFICATE", "False").lower() == "true"


# SPARQL prefixes
prefixes = '''
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX demo: <http://kg.demo.sap.com/>
'''

# Function to execute SPARQL queries
def call_sparql_execute(query: str):
    headers = '''Accept: application/sparql-results+csv\r\nContent-Type: application/sparql-query'''
    cursor = None
    try:
        conn = dbapi.connect(
            address=HANA_ADDRESS,
            port=HANA_PORT,
            user=HANA_USER,
            password=HANA_PASSWORD,
            encrypt=HANA_ENCRYPT,
            sslValidateCertificate=HANA_SSL_VALIDATE_CERTIFICATE
        )
        cursor = conn.cursor()
        result = cursor.callproc('SYS.SPARQL_EXECUTE', (query, headers, '?', None))
        response = result[2]
        cursor.close()
        conn.close()
        return response
    except dbapi.Error as e:
        if cursor:
            cursor.close()
        error_message = f"Error executing SPARQL query: {str(e)}"
        return error_message

# Function to convert SPARQL results to a DataFrame
def convert_to_dataframe(result):
    return pd.read_csv(io.StringIO(result)).fillna('')

# Function to generate SPARQL query using Generative AI
def generate_sparql_query(user_input, ontology):
    
    system_prompt = f'''
        You are an intelligent SPARQL assistant. Your task is to:
        - Understand the user's question in any language.
        - If the language is not supported or not recognized, respond only with this message:
        "Please enter your question in a prominent language such as English, Spanish, German, or French."
        - If supported, generate a SPARQL query using the ontology provided below.
        - The graph is directed: properties go from domain to range.
        - Always enclose literals in double quotes.
        - If `rdfs:label` exists for any class or entity, always retrieve the label using OPTIONAL clause.
        - Return your response strictly as valid JSON in the format below.

        JSON Response Format:
        {{
            "sparql_query": "SPARQL query here",
            "literals": [{{"literal": "literal value"}}],
            "triples_with_literals": [{{"triple": "subject predicate object (with literal)"}}]
        }}

        <ontology>
        {ontology}
        </ontology>

        Instructions:
        - Use the graph name: `<kgdocu_movies>` in every SPARQL query.
        - Only return the SPARQL query and metadata in the required JSON format.
        - Do not explain or comment unless explicitly asked.
        - Be accurate in the directionality of triples and respectful of literal types and label usage.
    '''


    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]
    response = chat.completions.create(
        model_name='gpt-4o',
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0
    )
    return json.loads(response.choices[0].message.content)

# Load ontology (this should be preloaded or fetched once)
ontology_query = prefixes + '''
CONSTRUCT
FROM <wiki_movies_ontology>
WHERE {?s ?p ?o}
'''
ontology_result = call_sparql_execute(ontology_query)

@app.route('/', methods=['GET', 'POST'])
def index():
    results = None
    columns = None
    user_query = None
    sparql_query = None
    message = None

    if request.method == 'POST':
        user_query = request.form.get('user_query')
        try:
            # Generate SPARQL query
            sparql_data = generate_sparql_query(user_query, ontology_result)
            sparql_query = sparql_data['sparql_query']

            # Execute SPARQL query
            sparql_result = call_sparql_execute(sparql_query)
            results_df = convert_to_dataframe(sparql_result)

            # Prepare results for rendering
            results = results_df.values.tolist()
            columns = results_df.columns.tolist()

            # Check if results are empty
            if not results:
                message = f"No records found for query: {user_query}"

        except Exception as e:
            message = f"Error: {str(e)}"

    return render_template(
        'index.html',
        results=results,
        columns=columns,
        user_query=user_query,
        sparql_query=sparql_query,
        message=message
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)