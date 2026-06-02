docker run -d \
       --name science_inorganic_existing_material \
       -e PORT=20161 \
       -p 20161:20161 \
       -e base_url=http://host.docker.internal:20166 \
       --add-host host.docker.internal:host-gateway \
       science_inorganic_existing_material:v1.0
