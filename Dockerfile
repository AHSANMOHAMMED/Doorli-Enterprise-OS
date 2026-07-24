FROM frappe/erpnext:v15.19.1

USER root
# Only copy the custom doorli_core app to keep the build lightweight and avoid OOM kills
COPY apps/doorli_core /home/frappe/frappe-bench/apps/doorli_core
RUN chown -R frappe:frappe /home/frappe/frappe-bench/apps/doorli_core

USER frappe
# Install the custom app into the bench
RUN cd /home/frappe/frappe-bench && \
    bench get-app doorli_core --resolve-deps
