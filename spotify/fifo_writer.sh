#!/bin/sh
# librespot's subprocess backend does NOT use a shell and splits on spaces,
# so metacharacters like ">" cannot be passed inline. This wrapper gives it a
# single, space-free command that writes the PCM stream into the FIFO.
exec cat > /tmp/librespot.pcm
