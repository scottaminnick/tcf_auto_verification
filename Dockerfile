# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install crucial system-level C-libraries for Geospatial & GRIB data
RUN apt-get update && apt-get install -y \
    wget \
    build-essential \
    libeccodes0 \
    libeccodes-dev \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Set Environment Variables so Python knows where to find GDAL
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Keep glibc from handing every large array back to the kernel.
# Decoding one MRMS field allocates ~374 MB transiently. At glibc's default
# mmap threshold each of those goes to mmap and is munmapped on free, so every
# scan faults in fresh zeroed pages: measured over one composite, 941k minor
# faults and ~20s, versus 163k and ~15s with these set (scratch/profile_decode_phase.py).
ENV MALLOC_MMAP_THRESHOLD_=1073741824
ENV MALLOC_TRIM_THRESHOLD_=1073741824

# Copy the requirements file and install Python libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app's code into the container
COPY . .

# Expose the port Streamlit uses
EXPOSE 8501

# Command to run the Streamlit app
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
