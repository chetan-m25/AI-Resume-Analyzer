// ==============================
// Select Elements
// ==============================

const dropArea = document.getElementById("dropArea");
const fileInput = document.getElementById("resume");
const fileName = document.getElementById("fileName");
const uploadForm = document.getElementById("uploadForm");
const loadingScreen = document.getElementById("loadingScreen");

// ==============================
// Open File Explorer
// ==============================

dropArea.addEventListener("click", () => {
  fileInput.click();
});

// ==============================
// Display Selected File Name
// ==============================

fileInput.addEventListener("change", () => {
  if (fileInput.files.length > 0) {
    const file = fileInput.files[0];

    if (file.type !== "application/pdf") {
      alert("Please select a PDF file.");

      fileInput.value = "";

      fileName.textContent = "No file selected";

      return;
    }

    fileName.textContent = "✅ " + file.name;
  }
});

// ==============================
// Prevent Default Drag Behavior
// ==============================

["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
  dropArea.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
});

// ==============================
// Highlight Drop Area
// ==============================

["dragenter", "dragover"].forEach((eventName) => {
  dropArea.addEventListener(eventName, () => {
    dropArea.classList.add("drag-active");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropArea.addEventListener(eventName, () => {
    dropArea.classList.remove("drag-active");
  });
});

// ==============================
// Handle Drop
// ==============================

dropArea.addEventListener("drop", (e) => {
  const files = e.dataTransfer.files;

  if (files.length === 0) return;

  const file = files[0];

  if (file.type !== "application/pdf") {
    alert("Only PDF files are allowed.");

    return;
  }

  fileInput.files = files;

  fileName.textContent = "✅ " + file.name;
});

// ==============================
// Show Loading Screen
// ==============================

uploadForm.addEventListener("submit", (e) => {
  if (fileInput.files.length === 0) {
    e.preventDefault();

    alert("Please select a resume first.");

    return;
  }

  loadingScreen.classList.remove("hidden");
});
