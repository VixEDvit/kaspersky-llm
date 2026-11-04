FROM vllm/vllm-openai-cpu:v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647

USER root
ENV HOME=/home/model
RUN mkdir -p /home/model && chown 10001:10001 /home/model
WORKDIR /home/model
COPY requirements.txt /opt/server-requirements.txt

USER 10001:10001
ENTRYPOINT ["vllm", "serve"]
