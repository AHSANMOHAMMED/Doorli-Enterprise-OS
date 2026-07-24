FROM frappe/erpnext:v15.19.1

# We switch to root to install python and node dependencies from the local apps
USER root

# Copy local source code over the pre-built apps
COPY apps/frappe /home/frappe/frappe-bench/apps/frappe
COPY apps/erpnext /home/frappe/frappe-bench/apps/erpnext
COPY apps/doorli_core /home/frappe/frappe-bench/apps/doorli_core

RUN chown -R frappe:frappe /home/frappe/frappe-bench/apps

USER frappe

# Build assets
RUN cd /home/frappe/frappe-bench && \
    bench build
