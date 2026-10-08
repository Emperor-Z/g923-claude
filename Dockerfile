# Container build of g923d. Needs the wheel's input device and /dev/uinput
# passed in; desktop notifications and sounds are unavailable inside it.
# See README > Container.
FROM python:3.13-slim AS build
RUN pip install --no-cache-dir uv
WORKDIR /src
COPY . .
RUN uv build --wheel

FROM python:3.13-slim
COPY --from=build /src/dist/*.whl /tmp/
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev linux-libc-dev \
 && pip install --no-cache-dir /tmp/*.whl \
 && apt-get purge -y gcc libc6-dev linux-libc-dev && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/*.whl
ENTRYPOINT ["g923d"]
