/* ==========================================================
   Student Dashboard
========================================================== */

const studentId = window.studentId || "";

const examStatusCache = {};

/* ================= FLASH AUTO REMOVE ================= */
document.querySelectorAll(".flash").forEach(flash => {

    setTimeout(() => {

        flash.style.transition = "0.5s";

        flash.style.opacity = "0";

        setTimeout(() => {

            flash.remove();

        }, 500);

    }, 4000);

});

/* ================= DARK MODE ================= */
function toggleDarkMode() {
    document.body.classList.toggle("dark-mode");
    localStorage.setItem(
        "darkMode",
        document.body.classList.contains("dark-mode")
    );
}

/* ================= CREATE START BUTTON ================= */
function createStartButton(examId) {
    const a = document.createElement("a");
    a.className = "btn btn-success";
    a.href = `/start_exam/${examId}`;
    a.textContent = "Start Exam";
    return a;
}

/* ================= Exam Information ================= */
function showExamInfo(button){

    document.getElementById("modalExamTitle").textContent =
        button.dataset.title;

    document.getElementById("modalSubject").textContent =
        button.dataset.subject;

    document.getElementById("modalExamType").textContent =
        button.dataset.examType;

    document.getElementById("modalTerm").textContent =
        button.dataset.term;

    document.getElementById("modalDuration").textContent =
        button.dataset.duration + " Minutes";

    document.getElementById("modalQuestions").textContent =
        button.dataset.questionCount + " Questions";

    document.getElementById("modalDescription").textContent =
        button.dataset.description || "No description available.";

    document.getElementById("startExamButton").href =
        button.dataset.startUrl;

    document.getElementById("examAcknowledgement").checked = false;

    document.getElementById("startExamButton")
            .classList.add("disabled");

    document.getElementById("startExamButton")
            .setAttribute("aria-disabled", "true");

}

/* ================= ACKNOWLEDGEMENT CHECKBOX ================= */
document.addEventListener("DOMContentLoaded", function () {

    const checkbox = document.getElementById("examAcknowledgement");
    const startButton = document.getElementById("startExamButton");

    if (!checkbox || !startButton) {
        return;
    }

    checkbox.addEventListener("change", function () {

        if (this.checked) {

            startButton.classList.remove("disabled");
            startButton.removeAttribute("aria-disabled");

        } else {

            startButton.classList.add("disabled");
            startButton.setAttribute("aria-disabled", "true");

        }

    });

});

/* ================= SET EXAM ID ================= */

let selectedExamId = null;

function setExamId(examId) {

    selectedExamId = examId;

    document.getElementById("modal_exam_id").value = examId;
}

/* ================= Show Sending State ================= */
function showSendingState() {

    if (!selectedExamId) return;

    const btn = document.getElementById(`request-btn-${selectedExamId}`);

    if (!btn) return;

    btn.disabled = true;

    btn.innerHTML = `
        <span class="spinner-border spinner-border-sm me-2"></span>
        Sending...
    `;

    const modal = bootstrap.Modal.getInstance(
        document.getElementById("requestExamModal")
    );

    if (modal) {
        modal.hide();
    }

}

/* ================= Update Exam Action v1 ================= */
function updateExamCard(exam) {

    const examId = exam.exam_id;

    const card = document.getElementById(`req-${examId}`);

    const prevStatus = examStatusCache[examId];
    examStatusCache[examId] = exam.status;

    if (exam.status === "approved" && prevStatus !== "approved") {
        if (card) {
            card.classList.add("approved-highlight");

            setTimeout(() => {
                card.classList.remove("approved-highlight");
            }, 5000);
        }
    }

    const statusBox = document.getElementById(`exam-status-${examId}`);
    const actionBox = document.getElementById(`exam-action-${examId}`);

    if (!statusBox || !actionBox) return;

    console.log("Updating Exam:", examId, exam.status);

    // ================= STATUS =================
    if (exam.status === "approved") {

        statusBox.innerHTML = `
            <span class="badge bg-success">
                🟢 Ready
            </span>
        `;

        actionBox.innerHTML = `
            <button
                type="button"
                class="btn btn-success"
                data-bs-toggle="modal"
                data-bs-target="#examInfoModal"

                data-title="${exam.title}"
                data-subject="${exam.subject}"
                data-description="${exam.description}"
                data-duration="${exam.duration}"
                data-question-count="${exam.question_count}"
                data-exam-type="${exam.exam_type}"
                data-term="${exam.term}"
                data-start-url="${exam.start_url}"

                onclick="showExamInfo(this)">
                Start Examination
            </button>
        `;

    }

    else if (exam.status === "completed") {

        statusBox.innerHTML = `
            <span class="badge bg-secondary">
                ✔ Completed
            </span>
        `;

        actionBox.innerHTML = `
            <button class="btn btn-secondary" disabled>
                Done
            </button>
        `;

    }

    else if (exam.status === "forced_submit") {

        statusBox.innerHTML = `
            <span class="badge bg-danger">
                ⛔ Force Submitted
            </span>
        `;

        actionBox.innerHTML = `
            <button class="btn btn-secondary" disabled>
                Done
            </button>
        `;

    }

    else if (exam.status === "pending") {

        statusBox.innerHTML = `
            <span class="badge bg-warning text-dark">
                🟡 Pending Approval
            </span>
        `;

        actionBox.innerHTML = `
            <div class="waiting-transition">

                <div class="text-warning fw-semibold d-flex align-items-center mb-2">
                    <span class="spinner-border spinner-border-sm me-2"></span>
                    Waiting for Approval...
                </div>

                <button
                    class="btn btn-outline-danger btn-sm w-100"
                    onclick="cancelExamRequest(${exam.exam_id})">

                    Cancel

                </button>

            </div>
        `;

        actionBox.classList.add("exam-update");

        setTimeout(() => {
            actionBox.classList.remove("exam-update");
        }, 600);

        }

    else if (
        exam.status === "not_requested" ||
        exam.status === "rejected"
    ) {

        statusBox.innerHTML = `
            <span class="badge bg-secondary">
                ⚪ Not Requested
            </span>
        `;

        actionBox.innerHTML = `
            <button class="btn btn-primary"
                    data-bs-toggle="modal"
                    data-bs-target="#requestExamModal"
                    onclick="setExamId(${examId})">
                Request Exam
            </button>
        `;

    }
}

/* ================= CANCEL EXAM REQUEST ================= */

function cancelExamRequest(examId) {

    fetch("/cancel-exam-request", {

        method: "POST",

        headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        },

        body: JSON.stringify({
            exam_id: examId
        })

    })

    .then(response => response.json())

    .then(data => {

        if (!data.success) {

            alert(data.message);
            return;

        }

        updateExamCard({

            exam_id: examId,
            status: "not_requested"

        });

        const toastElement =
            document.getElementById("liveToast");

        const toastMessage =
            document.getElementById("toastMessage");

        toastMessage.textContent = data.message;

        toastElement.classList.remove("text-bg-danger");
        toastElement.classList.add("text-bg-success");

        new bootstrap.Toast(toastElement).show();

    })

    .catch(error => {

        console.error("Cancel Request Error:", error);

    });

}

/* ================= LIVE EXAM STATUS ================= */
setInterval(function () {

    fetch("/student-exam-status")
        .then(response => response.json())
        .then(data => {

            console.log("LIVE STATUS:", data);

            data.forEach(updateExamCard);

        })
        .catch(error => {

            console.error("Status Check Error:", error);

        });

}, 5000);

/* ================= REQUEST EXAM ================= */
document.addEventListener("DOMContentLoaded", function () {

    const form = document.getElementById("requestExamForm");

    if (!form) {
        return;
    }

    form.addEventListener("submit", function (event) {

        event.preventDefault();

        const formData = new FormData(form);

        showSendingState();

        console.log("🚀 Sending request to:", form.action);

        console.log("Exam ID being sent:", formData.get("exam_id"));

        fetch(form.action, {

            method: "POST",

            body: new FormData(form),

            headers: {
                "X-Requested-With": "XMLHttpRequest"
            }

        })

        .then(response => response.json())

        .then(data => {

            if (data.success) {

                updateExamCard({

                    exam_id: selectedExamId,
                    status: "pending"

                });

                const toastElement =
                    document.getElementById("liveToast");

                const toastMessage =
                    document.getElementById("toastMessage");

                toastMessage.textContent = data.message;

                toastElement.classList.remove("text-bg-danger");
                toastElement.classList.add("text-bg-success");

                const toast = new bootstrap.Toast(toastElement, {

                    delay: 3000,
                    autohide: true

                });

                toast.show();

            }

        })

        .catch(error => {

            console.error("Fetch Error:", error);

        });

    });

});

/* ================= REAL-TIME SSE ================= */

if (!window.studentId) {

    console.warn("No student ID for SSE - stream disabled");

} else {

    const studentStream = new EventSource(`/stream/${studentId}`);

    studentStream.addEventListener("request_sent", function (e) {

        const data = JSON.parse(e.data);

        alert("Request sent! Waiting for approval...");

        console.log("Request sent:", data);

    });

    studentStream.addEventListener("approved", function (e) {

        const data = JSON.parse(e.data);

        console.log("Approved:", data);

        fetch("/student-dashboard-data")

            .then(response => response.json())

            .then(exams => {

                exams.forEach(updateExamCard);

                const toastElement =
                    document.getElementById("liveToast");

                const toastMessage =
                    document.getElementById("toastMessage");

                toastMessage.textContent =
                    "🎉 Your exam request has been approved!";

                toastElement.classList.remove("text-bg-success");
                toastElement.classList.add("text-bg-primary");

                const toast =
                    new bootstrap.Toast(toastElement);

                toast.show();

            })

            .catch(error => {

                console.error("SSE Refresh Error:", error);

            });

    });

    studentStream.onerror = function () {

        console.log("SSE disconnected or error");

    };

}

/* ================= Greetings ================= */
(function () {

    const greeting = document.getElementById("greetingTitle");

    if (!greeting) return;

    const hour = new Date().getHours();

    let message = "👋 Welcome";

    if (hour < 12) {

        message = "☀️ Good Morning";

    } else if (hour < 18) {

        message = "🌤️ Good Afternoon";

    } else {

        message = "🌙 Good Evening";

    }

    greeting.innerHTML = `${message}, ${window.studentName}`;

})();

/* ================= MOBILE SIDEBAR ================= */

(function () {

    const sidebar = document.getElementById("studentSidebar");
    const icon = document.getElementById("sidebarIcon");

    if (!sidebar || !icon) return;

    sidebar.addEventListener("show.bs.collapse", function () {

        icon.classList.remove("bi-list");
        icon.classList.add("bi-x-lg");

    });

    sidebar.addEventListener("hide.bs.collapse", function () {

        icon.classList.remove("bi-x-lg");
        icon.classList.add("bi-list");

    });

})();