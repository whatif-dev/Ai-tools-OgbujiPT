'''
Advanced, "Chat my PDF" demo

Use a PDF document as a knowledge base to provide context for natural language Q&A

UI: Streamlit - streamlit.io
Vector store: Qdrant - https://qdrant.tech/
    Alternatives: pgvector, Chroma, Faiss, Weaviate, etc.
PDF to text: PyPDF2
    Alternatives: pdfplumber
Text to vector (embedding) model: 
    Alternatives: https://www.sbert.net/docs/pretrained_models.html / OpenAI ada002

Single-PDF support, for now, to keep the demo code simple, 
though you can easily extend it to e.g. work with multiple docs
dropped in a directory

Based on https://github.com/wafflecomposite/langchain-ask-pdf-local
but taking advantage of OgbujiPT

You need access to an OpenAI-like service. Default assumption is that you
have a self-hosted framework such as llama-cpp-python or text-generation-webui
running. Assume for the following it's at my-llm-host:8000

Prerequisites. From OgbujiPT cloned dir:.

```sh
pip install --upgrade .
pip install streamlit watchdog PyPDF2 PyCryptodome sentence_transformers qdrant-client tiktoken
```

You'll probably need a .env file. See demo/.env for an example to copy. Run the demo:

```sh
streamlit run demo/chat_pdf_streamlit_ui.py
```

Something about Streamlit's event loop is interacting with asyncio to cause the `async_main` 
coroutine to be invoked twice. We might need to perhaps bundle our coroutines and 
`streamlit.bootstrap.run()` together in an `asyncio.gather()`,or something of the sort.
'''
import asyncio
import os

from dotenv import load_dotenv

import streamlit as st
from PyPDF2 import PdfReader

from ogbujipt.config import openai_emulation, openai_live, HOST_DEFAULT
from ogbujipt.prompting import format, CHATGPT_DELIMITERS
from ogbujipt import oapi_choice1_text
from ogbujipt.text_helper import text_splitter
from ogbujipt.embedding_helper import qdrant_collection

# LLM will be downloaded from HuggingFace automatically
# There seem to be reentrancy issues with HuggingFace; defer import
from sentence_transformers import SentenceTransformer

# import zlib for crc32 checksums
import zlib

# Avoid re-entrace complaints from huggingface/tokenizers
#os.environ['TOKENIZERS_PARALLELISM'] = 'false'

# Load the main parameters from .env file
load_dotenv()
# User can set a variety of likely values to trigger use of OpenAI full-service
OPENAI = os.getenv('OPENAI', 'False') in \
    ['True', 'true', 'TRUE', 'Yes', 'yes', 'YES']
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
LLM = os.getenv('LLM', 'LLM')  # TODO: get this from non-openai openai api hosts
LLM_HOST = os.getenv('LLM_HOST', 'my-llm-host')
LLM_PORT = os.getenv('LLM_PORT', '8000')
# LLM "temperature"
LLM_TEMP = float(os.getenv('LLM_TEMP', '1'))
# LLM max context size
N_CTX = int(os.getenv('N_CTX', '2048'))
# K - how many chunks to return for query context
K = int(os.getenv('K', '3'))
# Chunk size is the number of characters counted in the chunks
EMBED_CHUNK_SIZE = int(os.getenv('EMBED_CHUNK_SIZE', '500'))
# Chunk Overlap to connect ends of chunks together
EMBED_CHUNK_OVERLAP = int(os.getenv('EMBED_CHUNK_OVERLAP', '100'))
# small LM for embeddings
# default https://huggingface.co/sentence-transformers/all-MiniLM-L12-v2
DOC_EMBEDDINGS_LLM = os.getenv('EMBED_CHUNK_OVERLAP', 'all-MiniLM-L12-v2')

PDF_USER_QUERY_PROMPT = 'Ask a question about your PDF:'

# Streamlit uses caching to save on repeat loads
@st.cache_data
def load_throbber():
    # throbber.gif as a data URL
    # TODO: use a less archaic format than GIF; perhaps some sort of animatable vector image
    return 'data:image/gif;base64,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'  # noqa E501

# Streamlit uses caching to save on repeat loads
@st.cache_resource
def load_embedding_model(embedding_model_name):
    # LLM will be downloaded from HuggingFace automatically
    return SentenceTransformer(embedding_model_name)


def prep_pdf(pdf, embedding_model, collection_name):
    # Streamlit treats function docstrings as magic strings for user display
    # Describe function via comments instead:
    # Converts pdf content into chunks according to chunk size & overlap
    # Vectorizes chunks for sLLM lookup
    # returns `knowledge_base`, the vector DB with indexed chunks

    # Create in-memory Qdrant instance
    knowledge_base = qdrant_collection(collection_name, embedding_model)

    pdf_reader = PdfReader(pdf)

    # Collect text from pdf
    text = ''.join((page.extract_text() for page in pdf_reader.pages))

    # Split the text into chunks
    chunks = text_splitter(
        text, 
        chunk_size=EMBED_CHUNK_SIZE,
        chunk_overlap=EMBED_CHUNK_OVERLAP,
        separator='\n'
        )

    #print('\n\n'.join([ch[:100] for ch in chunks]))
    # Add a new collection for this document, and insert the chunks into it
    knowledge_base.update(texts=chunks)

    return knowledge_base


def streamlit_loop(openai_api, model, LLM_TEMP):
    # Streamlit treats function docstrings as magic strings for user display
    '''
    Oori — Ask your PDF 📄💬
    '''
    # Set up Streamlit page
    st.set_page_config(
        page_title='Ask your PDF',
        page_icon='📄💬',
        layout='wide',
        initial_sidebar_state='expanded',
        )

    # Create file upload box on Streamlit, set from the user's upload
    # Use st.session_state to avoid unnessisary reprocessing/reloading
    if 'pdf' not in st.session_state:  # First use and need to init the PDF
        pdf = st.file_uploader('Upload a PDF', type=['pdf'], accept_multiple_files=False)
        st.session_state['pdf'] = pdf

        new_pdf = True  # Flag to know if the new pdf needs to be embedded

    else:  # PDF does exist
        temp_pdf = st.file_uploader('Upload a PDF', type=['pdf'], accept_multiple_files=False)

        # Encode PDF content and encode them for comparison
        pdf_new_checksum = zlib.adler32(str(temp_pdf).encode('utf-8'))
        pdf_old_checksum = zlib.adler32(str(st.session_state['pdf']).encode('utf-8'))
        
        
        if pdf_new_checksum == pdf_old_checksum:  # PDF is the same
            pdf = st.session_state['pdf']
            new_pdf = False  # Flag to know if the new pdf needs to be embedded
            
        else: # PDF is now different and needs to swap out session_state
            pdf, st.session_state['pdf'] = temp_pdf, temp_pdf
            new_pdf = True  # Flag to know if the new pdf needs to be embedded
    
    if pdf:  # Only run once the program has a "pdf" loaded
        # Show throbber, embed the PDF, and get ready for similarity search
        placeholder = st.empty()
        
        # Load throbber from cache
        throbber = load_throbber()
        placeholder.image(throbber)

        # Get the embedding model
        embedding_model = load_embedding_model(DOC_EMBEDDINGS_LLM)
        
        # Prepare and embed a knowledgebase (kb) with the pdf as its contents
        # Use st.session_state to avoid unnessisary reprocessing/reloading
        if new_pdf:
            kb = prep_pdf(pdf, embedding_model, collection_name=pdf.name)
            st.session_state['kb'] = kb
        else:
            kb = st.session_state['kb']

        # Clear all elements in placeholder (in this case, just the throbber)
        placeholder.empty()

        # Get the user query
        user_query = st.text_input(PDF_USER_QUERY_PROMPT)
        if user_query:  # Only run once the program has a "user_query"
            docs = kb.search(user_query, limit=K)
            serg_rezulds = '\n\n'.join(doc.payload['_text'] for doc in docs if doc.payload)
            st.code(serg_rezulds, language="python", line_numbers=True)


def main():
    # Streamlit treats function docstrings as magic strings for user display
    # Describing function via comments instead:
    # Set up LLM host connection & launch the main loop
    # Use OpenAI API if specified, otherwise emulate with supplied host, etc.
    if OPENAI:
        assert not (LLM_HOST or LLM_PORT), 'Don\'t use --host or --port with --openai'
        model = LLM
        openai_api = openai_live(
            model=LLM, debug=True)
    else:
        # For now the model param is most useful when OPENAI is True
        model = LLM or HOST_DEFAULT
        openai_api = openai_emulation(
            host=LLM_HOST, port=LLM_PORT, model=LLM, debug=True)
        
    streamlit_loop(openai_api, model, LLM_TEMP)


# Streamlit requires a main call(?????)
main()