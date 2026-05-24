document.addEventListener("DOMContentLoaded", function () {
    const dropZone = document.getElementById("drop-zone");
    const fileInput = document.getElementById("file-input");
    const uploadPrompt = document.getElementById("upload-prompt");
    const uploadPreview = document.getElementById("upload-preview");
    const previewImage = document.getElementById("preview-image");
    const clearBtn = document.getElementById("clear-btn");
    const fileName = document.getElementById("file-name");
    const fileSize = document.getElementById("file-size");
    const submitBtn = document.getElementById("submit-btn");
    const pipelineItems = document.querySelectorAll(".pipeline-item");

    // Drag and Drop Events
    if (dropZone) {
        ["dragenter", "dragover", "dragleave", "drop"].forEach(eventName => {
            dropZone.addEventListener(eventName, preventDefaults, false);
        });

        function preventDefaults(e) {
            e.preventDefault();
            e.stopPropagation();
        }

        ["dragenter", "dragover"].forEach(eventName => {
            dropZone.addEventListener(eventName, () => dropZone.classList.add("dragover"), false);
        });

        ["dragleave", "drop"].forEach(eventName => {
            dropZone.addEventListener(eventName, () => dropZone.classList.remove("dragover"), false);
        });

        dropZone.addEventListener("drop", handleDrop, false);

        function handleDrop(e) {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files.length) {
                fileInput.files = files; // Update input
                handleFiles(files[0]);
            }
        }
    }

    // File Input Change Event
    if (fileInput) {
        fileInput.addEventListener("change", function () {
            if (this.files.length) {
                handleFiles(this.files[0]);
            }
        });
    }

    // Handle File Processing
    function handleFiles(file) {
        if (!file.type.startsWith("image/")) {
            alert("Please upload an image file.");
            return;
        }

        // Setup File Name & Size
        fileName.textContent = file.name;
        fileSize.textContent = (file.size / 1024).toFixed(2) + " KB";

        // Read and Preview Image
        const reader = new FileReader();
        reader.onload = function (e) {
            previewImage.src = e.target.result;
            uploadPrompt.classList.add("d-none");
            uploadPreview.classList.remove("d-none");
            
            // Enable Submit and update pipeline
            submitBtn.removeAttribute("disabled");
            updatePipeline(0); // Image Uploaded
        };
        reader.readAsDataURL(file);
    }

    // Clear Button
    if (clearBtn) {
        clearBtn.addEventListener("click", function (e) {
            e.stopPropagation(); // Prevent triggering file input click
            fileInput.value = "";
            previewImage.src = "";
            uploadPrompt.classList.remove("d-none");
            uploadPreview.classList.add("d-none");
            submitBtn.setAttribute("disabled", "true");
            resetPipeline();
        });
    }

    // Form Submit Event (Simulate AI Pipeline)
    const form = document.getElementById("upload-form");
    if (form) {
        form.addEventListener("submit", function (e) {
            // We don't prevent default, but we can show animation before the page unloads
            submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span> Processing...';
            submitBtn.setAttribute("disabled", "true");
            
            // Start pipeline simulation (will be cut short by page navigation, but looks cool)
            let step = 1;
            const interval = setInterval(() => {
                if (step < pipelineItems.length) {
                    updatePipeline(step);
                    step++;
                } else {
                    clearInterval(interval);
                }
            }, 800);
        });
    }

    function updatePipeline(stepIndex) {
        pipelineItems.forEach((item, index) => {
            item.classList.remove("active", "processing");
            if (index < stepIndex) {
                item.classList.add("active");
            } else if (index === stepIndex) {
                item.classList.add("processing");
            }
        });
    }

    function resetPipeline() {
        pipelineItems.forEach(item => {
            item.classList.remove("active", "processing");
        });
    }
});
