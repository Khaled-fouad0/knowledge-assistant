import streamlit as st
import requests

API_URL = "https://web-production-f6c12.up.railway.app"
st.set_page_config(page_title="AI Knowledge Assistant", page_icon="🤙🏽", layout="centered")
st.title("AI Knowledge Assistant")
st.caption("Upload a document and ask questions about it")
SESSION_ID = "streamlit_user"
st.subheader("Upload Your Document")

uploaded_file = st.file_uploader("Choose a PDF file", type=["pdf"])
if uploaded_file:
    with st.spinner("Building knowledge base..."):
        response = requests.post(
            f"{API_URL}/upload",
            files={"file": (uploaded_file.name, uploaded_file, "application/pdf")}
        )
        data = response.json()
    if data["status"] == "success":
        st.success(f"Ready, {data['pages']} pages, {data['chunks']} chunks indexed.")
        st.session_state["file_ready"] = True
if st.session_state.get("file_ready"):
    st.subheader("Ask Questions:")
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    if question := st.chat_input("Ask somthing about your document:"):
        st.session_state["messages"].append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = requests.post(
                    f"{API_URL}/ask",
                    json={"message": question, "session_id": SESSION_ID}
                )
                answer = response.json()["answer"]
            st.markdown(answer)
        st.session_state["messages"].append({"role": "assistant", "content": answer})
    if st.button("Clear conversation"):
        requests.delete(f"{API_URL}/reset/{SESSION_ID}")
        st.session_state["messages"] = []
        st.rerun()