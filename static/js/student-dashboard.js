/* ==========================================================
   Student Dashboard - FIXED VERSION
   - Manual refresh button with press reaction
   - Dynamic exam creation (not just update)
   ========================================================== */

const studentId = window.studentId || "";
const examStatusCache = {};

function getCsrfToken(){
  const meta = document.querySelector('meta[name="csrf-token"]');
  if(meta) return meta.content;
  const input = document.querySelector('input[name="csrf_token"]');
  return input ? input.value : "";
}
async function safeJsonFetch(url, options={}){
  // Always attach CSRF token as header too
  const csrfToken = getCsrfToken();
  const baseHeaders = {
    'Accept': 'application/json',
    'X-Requested-With': 'XMLHttpRequest'
  };
  if(csrfToken){
    baseHeaders['X-CSRFToken'] = csrfToken;
    baseHeaders['X-CSRF-TOKEN'] = csrfToken;
  }
  // Merge user headers AFTER base so they don't overwrite Accept/X-Requested-With
  const userHeaders = options.headers || {};
  const finalHeaders = { ...baseHeaders, ...userHeaders };
  // Ensure Accept and X-Requested-With are always present even if user overwrote
  finalHeaders['Accept'] = 'application/json';
  finalHeaders['X-Requested-With'] = 'XMLHttpRequest';

  const { headers, ...restOptions } = options;
  const res = await fetch(url, {
    credentials: 'include',
    ...restOptions,
    headers: finalHeaders
  });
  const text = await res.text();
  const ctype = res.headers.get('content-type')||'';
  if(!ctype.includes('application/json') || text.trim().startsWith('<!DOCTYPE') || text.trim().startsWith('<html')){
    console.error(`HTML returned for ${url}:`, text.substring(0,600));
    throw new Error(text.includes('login') ? 'Session expired - reload page' : `Server returned HTML (${res.status}) - CSRF or route error`);
  }
  return { ok: res.ok, status: res.status, json: JSON.parse(text) };
}

/* ================= FLASH AUTO REMOVE ================= */
document.querySelectorAll(".flash").forEach(flash => {
    setTimeout(() => {
        flash.style.transition = "0.5s";
        flash.style.opacity = "0";
        setTimeout(() => { flash.remove(); }, 500);
    }, 4000);
});

/* ================= DARK MODE ================= */
function toggleDarkMode() {
    document.body.classList.toggle("dark-mode");
    localStorage.setItem("darkMode", document.body.classList.contains("dark-mode"));
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
    document.getElementById("modalExamTitle").textContent = button.dataset.title;
    document.getElementById("modalSubject").textContent = button.dataset.subject;
    document.getElementById("modalExamType").textContent = button.dataset.examType;
    document.getElementById("modalTerm").textContent = button.dataset.term;
    document.getElementById("modalDuration").textContent = button.dataset.duration + " Minutes";
    document.getElementById("modalQuestions").textContent = button.dataset.questionCount + " Questions";
    document.getElementById("modalDescription").textContent = button.dataset.description || "No description available.";
    document.getElementById("startExamButton").href = button.dataset.startUrl;
    document.getElementById("examAcknowledgement").checked = false;
    document.getElementById("startExamButton").classList.add("disabled");
    document.getElementById("startExamButton").setAttribute("aria-disabled", "true");
}

/* ================= ACKNOWLEDGEMENT CHECKBOX ================= */
document.addEventListener("DOMContentLoaded", function () {
    const checkbox = document.getElementById("examAcknowledgement");
    const startButton = document.getElementById("startExamButton");
    if (!checkbox || !startButton) return;
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

/* ================= Force Close Modal + Backdrop Fix ================= */
function forceCloseModal(modalId){
    const el = document.getElementById(modalId);
    if(!el) return;
    try{
        const instance = bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
        instance.hide();
    }catch(e){}
    // Bootstrap 5 sometimes leaves backdrop - force remove after animation
    setTimeout(()=>{
        document.querySelectorAll('.modal-backdrop').forEach(b=> b.remove());
        document.body.classList.remove('modal-open');
        document.body.style.overflow = '';
        document.body.style.paddingRight = '';
        document.documentElement.style.overflow = '';
    }, 350);
}

/* ================= Show Sending State ================= */
function showSendingState() {
    if (!selectedExamId) return;
    const btn = document.getElementById(`request-btn-${selectedExamId}`);
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>Sending...`;
    forceCloseModal("requestExamModal");
}

/* ================= CREATE EXAM CARD (NEW) ================= */
function createExamCardElement(exam) {
    const wrapper = document.createElement("div");
    wrapper.id = `req-${exam.exam_id}`;
    wrapper.className = "list-group-item exam-card d-flex justify-content-between align-items-center";
    wrapper.style.animation = "cardSlideIn 0.45s cubic-bezier(.34,1.56,.64,1)";
    const noMsg2 = document.getElementById("noExamsMsg");
    if (noMsg2) noMsg2.style.display = "none";
    wrapper.innerHTML = `
        <div><h6 class="mb-1">${exam.title}</h6><small class="text-muted">Subject: ${exam.subject || "N/A"}</small></div>
        <div id="exam-status-${exam.exam_id}" class="mb-2"><span class="badge bg-secondary">⚪ Not Requested</span></div>
        <div id="exam-action-${exam.exam_id}"><button class="btn btn-secondary" disabled>Loading...</button></div>
    `;
    return wrapper;
}

/* ================= Update Exam Action ================= */
function updateExamCard(exam) {
    const examId = exam.exam_id;
    let card = document.getElementById(`req-${examId}`);
    const listGroup = document.getElementById("examListContainer") || document.querySelector("#exam-section .list-group");
    const noMsg = document.getElementById("noExamsMsg");
    

    // If card doesn't exist (new exam published), create it
    if (!card && listGroup) {
        console.log(`[CREATE] New exam card for ${examId}: ${exam.title}`);
        card = createExamCardElement(exam);
        listGroup.appendChild(card);
        // Remove "No exams available" placeholder if present
        const placeholder = document.querySelector("#exam-section > p");
        if (placeholder) placeholder.remove();
    }

    const prevStatus = examStatusCache[examId];
    examStatusCache[examId] = exam.status;

    if (exam.status === "approved" && prevStatus !== "approved") {
        if (card) {
            card.classList.add("approved-highlight");
            setTimeout(() => { card.classList.remove("approved-highlight"); }, 5000);
        }
    }

    const statusBox = document.getElementById(`exam-status-${examId}`);
    const actionBox = document.getElementById(`exam-action-${examId}`);
    if (!statusBox || !actionBox) return;

    console.log("Updating Exam:", examId, exam.status);

    if (exam.status === "approved") {
        statusBox.innerHTML = `<span class="badge bg-success">🟢 Ready</span>`;
        actionBox.innerHTML = `
            <button type="button" class="friendly-btn success" data-flip="y" data-bs-toggle="modal" data-bs-target="#examInfoModal"
                data-title="${exam.title}" data-subject="${exam.subject}" data-description="${exam.description}"
                data-duration="${exam.duration}" data-question-count="${exam.question_count}"
                data-exam-type="${exam.exam_type}" data-term="${exam.term}" data-start-url="${exam.start_url}"
                onclick="showExamInfo(this)">Start Examination</button>`;
    }
    else if (exam.status === "completed") {
        statusBox.innerHTML = `<span class="badge bg-secondary">✔ Completed</span>`;
        actionBox.innerHTML = `<button class="btn btn-secondary" disabled>Done</button>`;
    }
    else if (exam.status === "forced_submit") {
        statusBox.innerHTML = `<span class="badge bg-danger">⛔ Force Submitted</span>`;
        actionBox.innerHTML = `<button class="btn btn-secondary" disabled>Done</button>`;
    }
    else if (exam.status === "pending") {
        statusBox.innerHTML = `<span class="badge bg-warning text-dark">🟡 Pending Approval</span>`;
        actionBox.innerHTML = `
            <div class="waiting-transition">
                <div class="text-warning fw-semibold d-flex align-items-center mb-2">
                    <span class="spinner-border spinner-border-sm me-2"></span>Waiting for Approval...
                </div>
                <button class="btn btn-outline-danger btn-sm w-100" onclick="cancelExamRequest(${exam.exam_id})">Cancel</button>
            </div>`;
        actionBox.classList.add("exam-update");
        setTimeout(() => { actionBox.classList.remove("exam-update"); }, 600);
    }
    else if (exam.status === "not_requested" || exam.status === "rejected") {
        statusBox.innerHTML = exam.status === "rejected"
            ? `<span class="badge bg-danger">❌ Rejected</span>`
            : `<span class="badge bg-secondary">⚪ Not Requested</span>`;
        actionBox.innerHTML = `<button id="request-btn-${exam.exam_id}" class="friendly-btn primary" data-flip="spin" data-bs-toggle="modal" data-bs-target="#requestExamModal" onclick="setExamId(${exam.exam_id})">Request Exam</button>`;
    }
}

/* ================= CANCEL REQUEST ================= */
function cancelExamRequest(examId) {
    fetch("/cancel-exam-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exam_id: examId })
    })
    .then(r => r.json())
    .then(data => {
        if (!data.success) { alert(data.message); return; }
        updateExamCard({ exam_id: examId, status: "not_requested" });
        const toastEl = document.getElementById("liveToast");
        const toastMsg = document.getElementById("toastMessage");
        toastMsg.textContent = data.message;
        toastEl.classList.remove("text-bg-danger");
        toastEl.classList.add("text-bg-success");
        new bootstrap.Toast(toastEl).show();
    })
    .catch(e => console.error("Cancel Request Error:", e));
}

/* ================= MANUAL REFRESH - WITH PRESS REACTION ================= */
window.refreshAvailableExams = function refreshAvailableExams() {
    const btn = document.getElementById("refreshExamsBtn");
    if (!btn) return;
    
    // --- PRESS REACTION ---
    btn.classList.add("pressed", "loading");
    btn.style.transform = "scale(0.88)";
    
    // Haptic feedback if available
    if (navigator.vibrate) navigator.vibrate(50);
    
    const icon = btn.querySelector("i");
    const originalClass = icon ? icon.className : "bi bi-arrow-repeat";
    
    // Ripple effect
    const ripple = document.createElement("span");
    ripple.className = "btn-ripple";
    ripple.style.cssText = "position:absolute; inset:0; border-radius:50%; background:rgba(255,255,255,0.4); animation: rippleAnim 0.6s ease-out;";
    btn.style.position = "relative";
    btn.style.overflow = "hidden";
    btn.appendChild(ripple);
    
    // Fade existing cards
    document.querySelectorAll(".exam-card").forEach((card, idx) => {
        setTimeout(() => { card.style.opacity = "0.5"; card.style.transform = "scale(0.98)"; }, idx * 60);
    });

    fetch("/student-exam-status", { headers: { "Cache-Control": "no-cache" } })
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(data => {
            console.log("MANUAL REFRESH:", data);
            
            if (!Array.isArray(data)) data = [];
            
            // Handle empty
            if (data.length === 0) {
                console.warn("No exams returned - check enrollment / is_active");
                const listGroup = document.getElementById("examListContainer") || document.querySelector("#exam-section .list-group");
    const noMsg = document.getElementById("noExamsMsg");
    
                if (listGroup && listGroup.children.length === 0) {
                    // keep placeholder or show hint
                }
            }
            
            // Create/update all exams
            data.forEach(exam => updateExamCard(exam));
            
            // Also remove cards that no longer exist? Optional - keep for now
            
            // Success visual
            btn.style.background = "linear-gradient(135deg, #3daa5e 0%, #6bcf87 100%)";
            btn.style.borderColor = "#3daa5e";
            btn.style.color = "#fff";
            if (icon) icon.className = "bi bi-check-lg";
            
            // Restore cards
            document.querySelectorAll(".exam-card").forEach((card, idx) => {
                setTimeout(() => {
                    card.style.opacity = "1";
                    card.style.transform = "translateY(0) scale(1)";
                }, idx * 80);
            });

            // Toast
            const toastEl = document.getElementById("liveToast");
            const toastMsg = document.getElementById("toastMessage");
            if (toastEl && toastMsg) {
                if (data.length === 0) {
                    toastMsg.textContent = "⚠️ No exams found - check if you're enrolled in subjects with active exams";
                    toastEl.classList.remove("text-bg-success", "text-bg-primary");
                    toastEl.classList.add("text-bg-warning");
                } else {
                    toastMsg.textContent = `✅ Refreshed ${data.length} exam(s)`;
                    toastEl.classList.remove("text-bg-danger", "text-bg-warning");
                    toastEl.classList.add("text-bg-success");
                }
                new bootstrap.Toast(toastEl, { delay: 3000, autohide: true }).show();
            }

            setTimeout(() => {
                btn.style.background = "";
                btn.style.borderColor = "";
                btn.style.color = "";
                if (icon) icon.className = originalClass;
            }, 1300);
        })
        .catch(error => {
            console.error("Refresh Error:", error);
            const toastEl = document.getElementById("liveToast");
            const toastMsg = document.getElementById("toastMessage");
            if (toastEl && toastMsg) {
                toastMsg.textContent = "⚠️ Failed to refresh. Check console: " + error.message;
                toastEl.classList.remove("text-bg-success", "text-bg-primary");
                toastEl.classList.add("text-bg-danger");
                new bootstrap.Toast(toastEl).show();
            }
            // restore
            document.querySelectorAll(".exam-card").forEach(card => {
                card.style.opacity = "1"; card.style.transform = "scale(1)";
            });
        })
        .finally(() => {
            setTimeout(() => {
                btn.classList.remove("loading", "pressed");
                btn.style.transform = "";
                ripple.remove();
            }, 700);
        });
}

/* ================= REQUEST EXAM ================= */
document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("requestExamForm");
    if (!form) return;
    form.addEventListener("submit", function (event) {
        event.preventDefault();
        showSendingState();
        const formData = new FormData(form);
        const token = getCsrfToken();
        if(token && !formData.has('csrf_token')){
            formData.append('csrf_token', token);
        }

        safeJsonFetch(form.action, {
            method: "POST",
            body: formData,
        })
        .then(({ok, json}) => {
            // Always force cleanup backdrop
            forceCloseModal("requestExamModal");
            if (ok && json.success) {
                updateExamCard({ exam_id: selectedExamId, status: "pending", title: "", subject: "", description: "", duration: 0, question_count: 0, exam_type: "", term: "", start_url: "" });
                const toastEl = document.getElementById("liveToast");
                const toastMsg = document.getElementById("toastMessage");
                if(toastEl && toastMsg){
                    toastMsg.textContent = json.message;
                    toastEl.classList.remove("text-bg-danger");
                    toastEl.classList.add("text-bg-success");
                    new bootstrap.Toast(toastEl, { delay: 3000, autohide: true }).show();
                }
            } else {
                // Restore button
                const btn = document.getElementById(`request-btn-${selectedExamId}`);
                if (btn) { btn.disabled = false; btn.innerHTML = `Request Exam`; }
                const toastEl = document.getElementById("liveToast");
                const toastMsg = document.getElementById("toastMessage");
                if(toastEl && toastMsg){
                    toastMsg.textContent = json.message || "Request failed";
                    toastEl.classList.remove("text-bg-success");
                    toastEl.classList.add("text-bg-danger");
                    new bootstrap.Toast(toastEl, { delay: 3500, autohide: true }).show();
                } else {
                    alert(json.message || "Request failed");
                }
            }
        })
        .catch(e => {
            console.error("Fetch Error:", e);
            forceCloseModal("requestExamModal");
            const btn = document.getElementById(`request-btn-${selectedExamId}`);
            if (btn) { btn.disabled = false; btn.textContent = "Request Exam"; }
        });
    });

    // Also cleanup when modal is hidden via X or Cancel
    const reqModalEl = document.getElementById("requestExamModal");
    if(reqModalEl){
        reqModalEl.addEventListener('hidden.bs.modal', ()=>{
            document.querySelectorAll('.modal-backdrop').forEach(b=> b.remove());
            document.body.classList.remove('modal-open');
            document.body.style.overflow = '';
            document.body.style.paddingRight = '';
        });
    }
});

/* ================= REAL-TIME SSE ================= */
if (!window.studentId) {
    console.warn("No student ID for SSE - stream disabled");
} else {
    const studentStream = new EventSource(`/stream/${studentId}`);
    studentStream.addEventListener("request_sent", function (e) {
        const data = JSON.parse(e.data);
        console.log("Request sent:", data);
    });
    studentStream.addEventListener("approved", function (e) {
        const data = JSON.parse(e.data);
        console.log("Approved:", data);
        fetch("/student-dashboard-data")
        safeJsonFetch("/student-dashboard-data")
            .then(({json: exams}) => {
                console.log("MANUAL REFRESH:", exams);
                exams.forEach(exam => updateExamCard(exam));
                // your existing toast success code here
            })
            .catch(err => console.error("SSE Refresh Error:", err));
    });
    studentStream.onerror = function () { console.log("SSE disconnected or error"); };
}

/* ================= Greetings ================= */
(function () {
    const greeting = document.getElementById("greetingTitle");
    if (!greeting) return;
    const hour = new Date().getHours();
    let message = "👋 Welcome";
    if (hour < 12) message = "☀️ Good Morning";
    else if (hour < 18) message = "🌤️ Good Afternoon";
    else message = "🌙 Good Evening";
    greeting.innerHTML = `${message}, ${window.studentName}`;
})();

/* ================= MOBILE SIDEBAR ================= */
(function () {
    const sidebar = document.getElementById("studentSidebar");
    const toggle = document.querySelector('[data-bs-target="#studentSidebar"]');
    const icon = document.getElementById("sidebarIcon");
    if (!sidebar || !toggle || !icon) return;
    toggle.addEventListener("click", function () {
        setTimeout(() => {
            const isOpen = sidebar.classList.contains("show");
            icon.classList.toggle("bi-list", !isOpen);
            icon.classList.toggle("bi-x-lg", isOpen);
        }, 20);
    });
})();
