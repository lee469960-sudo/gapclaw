ARG BASE_IMAGE=myclaw-base:latest
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${BASE_IMAGE} AS myclaw_base
FROM ${PYTHON_IMAGE}
WORKDIR /workplace
COPY --from=myclaw_base /engine /engine
COPY --from=myclaw_base /skills /skills
RUN mkdir -p /workplace /mcp /llm
CMD ["sleep", "infinity"]
