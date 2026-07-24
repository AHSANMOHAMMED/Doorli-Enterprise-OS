FROM frappe/erpnext:v15.19.1

USER root
# Only copy the custom doorli_core app to keep the build lightweight and avoid OOM kills
COPY apps/doorli_core /home/frappe/frappe-bench/apps/doorli_core
RUN chown -R frappe:frappe /home/frappe/frappe-bench/apps/doorli_core

USER frappe
# Install the custom app manually since it's already copied (bypasses git clone)
RUN echo "doorli_core" >> /home/frappe/frappe-bench/sites/apps.txt && \
    /home/frappe/frappe-bench/env/bin/pip install -q -U -e /home/frappe/frappe-bench/apps/doorli_core
