// ============================================================
// EXAM ENGINE v2.0
// ------------------------------------------------------------
// This file controls the entire online examination system.
//
// Modules
// --------
// 1. Initialization
// 2. Countdown Timer
// 3. Answer Selection
// 4. Auto Save
// 5. Navigation
// 6. Review Answers
// 7. Submit Exam
// 8. Keyboard Shortcuts
//
// Author:
// Herculyses Piloton
//
// ============================================================

console.log("exam-engine.js loaded");

const Exam = {

    startFullscreen(event, el) {

        event.preventDefault();

        const go = async () => {

            try {

                // Request fullscreen (must be user-triggered)
                if (document.documentElement.requestFullscreen) {
                    await document.documentElement.requestFullscreen();
                }

            } catch (err) {
                console.warn("Fullscreen request failed:", err);
            }

            // Continue to exam after fullscreen attempt
            window.location.href = el.href;
        };

        go();

        return false;
    },

    // Prevent multiple submissions
    isSubmitting: false,
    securityModalShown: false,

    // ========================================================
    // Initialize the Exam Engine
    // ========================================================

    init() {

        console.log("Exam Engine Initialized");

        this.timer.start();

        this.answers.restore();

        this.navigator.load();

        this.security.init();

        this.live.init();

        const dashboardBtn = document.getElementById("backToDashboardBtn");

        if (dashboardBtn) {

            dashboardBtn.addEventListener("click", () => {

                window.location.href = "/dashboard/student";

            });

        }

    },

    // ========================================================
    // Countdown Timer Module
    // ========================================================

    timer: {

        timeLeft: 0,

        timerInterval: null,

        start() {

            this.timeLeft =
                parseInt(document.getElementById("remainingSeconds").value);

            this.update();

            this.interval =
                setInterval(() => this.update(), 1000);

        },

        update() {

            if (this.timeLeft <= 0) {

                document.getElementById("timer").innerText = "00:00";

                clearInterval(this.timerInterval);

                // Prevent duplicate auto-submit
                if (!Exam.isSubmitting) {

                    Exam.isSubmitting = true;

                    Exam.submit.finish(true);

                }

                return;

            }

            const minutes =
                Math.floor(this.timeLeft / 60);

            const seconds =
                this.timeLeft % 60;

            document.getElementById("timer").innerText =
                `${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}`;

            this.timeLeft--;

        }

    },

    // ========================================================
    // Student Answers Module
    // ========================================================

    answers: {

        // ----------------------------------------------
        // Highlight a selected answer
        // ----------------------------------------------

        highlight(button){

            document.querySelectorAll(".option-btn").forEach(btn=>{

                btn.classList.remove("btn-primary");

                btn.classList.add("btn-outline-primary");

            });

            button.classList.remove("btn-outline-primary");

            button.classList.add("btn-primary");

        },

        // ----------------------------------------------
        // Save answer to hidden field
        // ----------------------------------------------

        set(answer){

            document.getElementById("selectedAnswer").value =
                answer;

        },

        // ----------------------------------------------
        // Save answer into database
        // ----------------------------------------------

        async save(answer) {

            try {

                const response = await fetch("/save-answer",{

                    method:"POST",

                    headers:{
                        "Content-Type":"application/json"
                    },

                    body:JSON.stringify({

                        question_id:
                            document.getElementById("questionId").value,

                        answer:answer

                    })

                });

                if (!response.ok) {

                    console.error("Unable to save answer.");

                    return;

                }

                Exam.toast.show("✓ Answer Saved");

            }

            catch (error) {

                console.error(error);

            }

            return {
                question_id: document.getElementById("questionId").value,
                answer: answer
            };

        },

        // ----------------------------------------------
        // Student selected an answer
        // ----------------------------------------------

        async select(answer, button) {

            // Store selected answer locally
            document.getElementById("selectedAnswer").value = answer;

            // Remove previous highlight
            document.querySelectorAll(".option-btn").forEach(btn => {

                btn.classList.remove("btn-primary");
                btn.classList.add("btn-outline-primary");

            });

            // Highlight current answer
            button.classList.remove("btn-outline-primary");
            button.classList.add("btn-primary");

            // Save answer automatically
            const result = await this.save(answer);

            Exam.navigator.updateSingle(result.question_id, true);

        },

        // ----------------------------------------------
        // Restore previously saved answer
        // ----------------------------------------------

        restore() {

            const savedAnswer =
                document.getElementById("selectedAnswer").value;

            if (!savedAnswer) return;

            document.querySelectorAll(".option-btn").forEach(btn => {

                if (btn.dataset.answer === savedAnswer) {

                    btn.classList.remove("btn-outline-primary");
                    btn.classList.add("btn-primary");

                }

            });

        }

    },

    // ========================================================
    // Question Navigator Module
    // ========================================================

    navigator: {

        // ----------------------------------------------
        // Load navigator data
        // ----------------------------------------------

        async load() {

            try {

                const response =
                    await fetch(reviewAnswersURL);

                const reviewData =
                    await response.json();

                this.render(reviewData, "questionNavigator");
                this.updateSummary(reviewData);

            }

            catch(error) {

                console.error(error);

            }

        },

        render(reviewData, containerId) {

            let html = "";

            reviewData.forEach(item => {

                let buttonClass =
                    item.answered
                        ? "btn-success"
                        : "btn-outline-danger";

                // Highlight the current question
                let currentClass = "";

                if (item.question_id === currentQuestionId) {

                    currentClass = "current-question";

                }

                html += `

                <button
                    data-id="${item.question_id}"
                    class="btn btn-sm m-100 ${buttonClass} ${currentClass}"
                    onclick="Exam.navigation.go('${item.url}')">
                    ${item.question_number}
                </button>

                `;

            });

            document.getElementById(containerId).innerHTML = html;

        },

        // ----------------------------------------------
        // Live update single question status
        // ----------------------------------------------

        updateSingle(questionId, answered) {

            const buttons =
                document.querySelectorAll("#questionNavigator button");

            buttons.forEach(btn => {

                const match =
                    btn.getAttribute("data-id") == questionId;

                if (!match) return;

                // Remove old state classes
                btn.classList.remove("btn-outline-danger");
                btn.classList.remove("btn-success");

                btn.classList.add("nav-pulse");

                setTimeout(() => {
                    btn.classList.remove("nav-pulse");
                }, 350);

                // Apply new state
                btn.classList.add(
                    answered ? "btn-success" : "btn-outline-danger"
                );

            });

        },

        // ----------------------------------------------
        // Update exam summary panel
        // ----------------------------------------------

        updateSummary(reviewData) {

            let answered = 0;
            let total = reviewData.length;

            reviewData.forEach(item => {
                if (item.answered) answered++;
            });

            let remaining = total - answered;
            let percent = Math.round((answered / total) * 100);

            const summary = `
                Answered: ${answered} |
                Remaining: ${remaining} |
                Progress: ${percent}%
            `;

            const el = document.getElementById("examSummary");

            if (el) {
                el.innerHTML = summary;
            }

        }

    },

    // ========================================================
    // Navigation Module
    // Handles movement between questions.
    // ========================================================

    navigation: {

        // ----------------------------------------------
        // Go to another question
        // ----------------------------------------------

        go(url) {

            const card =
                document.getElementById("questionCard");

            // If no animation target exists, just navigate
            if (!card) {

                window.location.href = url;
                return;

            }

            card.classList.add("fade-out");

            setTimeout(() => {

                window.location.href = url;

            }, 250);

        }

    },

    // ========================================================
    // Review Answers Module
    // ========================================================

    review: {

        // ----------------------------------------------
        // Open Review Answers
        // ----------------------------------------------

        async open() {

            try {

                const response = await fetch(reviewAnswersURL);

                if (!response.ok) {

                    throw new Error("Unable to load review data.");

                }

                const reviewData = await response.json();
                const answered =
                    reviewData.filter(item => item.answered).length;

                const remaining =
                    reviewData.length - answered;

                const completion =
                    Math.round((answered / reviewData.length) * 100);

                let navigator = "";

                reviewData.forEach(item => {

                    navigator += `

                    <button
                        class="btn btn-sm m-1
                            ${item.answered
                                ? "btn-success"
                                : "btn-outline-danger"}"

                        onclick="Exam.review.go('${item.url}')">

                        ${item.question_number}

                    </button>

                    `;

                });

                // Build the review table
                let html = `
                <div class="card border-success mb-3">

                    <div class="card-header fw-bold">

                        📋 Exam Review Summary

                    </div>

                    <div class="card-body">

                        <div class="row text-center">

                            <div class="col">

                                <h4 class="text-success">

                                    ${answered}

                                </h4>

                                <small>Answered</small>

                            </div>

                            <div class="col">

                                <h4 class="text-danger">

                                    ${remaining}

                                </h4>

                                <small>Remaining</small>

                            </div>

                            <div class="col">

                                <h4 class="text-primary">

                                    ${completion}%

                                </h4>

                                <small>Completion</small>

                            </div>

                        </div>

                    </div>

                </div>

                <div class="card mb-3">

                    <div class="card-header fw-bold">

                        🧭 Question Navigator

                    </div>

                    <div class="card-body">

                        ${navigator}

                    </div>

                </div>

                <table class="table table-hover align-middle">

                    <thead>

                        <tr>

                            <th>#</th>

                            <th>Status</th>

                            <th>Your Answer</th>

                        </tr>

                    </thead>

                    <tbody>

                `;

                reviewData.forEach(item => {

                    html += `

                    <tr
                        style="cursor:pointer"
                        onclick="Exam.review.go('${item.url}')">

                        <td>

                            ${item.question_number}

                        </td>

                        <td>

                            ${
                                item.answered
                                    ? '<span class="badge bg-success">Answered</span>'
                                    : '<span class="badge bg-danger">Not Answered</span>'
                            }

                        </td>

                        <td>

                            ${item.selected_answer || "—"}

                        </td>

                    </tr>

                    `;

                });

                html += `

                    </tbody>

                </table>

                `;

                // Insert into the modal
                document.getElementById("reviewContent").innerHTML = html;
                // Open the Bootstrap modal
                const modal = new bootstrap.Modal(

                    document.getElementById("reviewModal")

                );

                modal.show();

            }

            catch(error){

                console.error(error);

                Exam.toast.show("Unable to load review.");

            }

        },

        // ----------------------------------------------
        // Go to a selected question
        // ----------------------------------------------

        go(url) {

            const modalElement =
                document.getElementById("reviewModal");

            const modal =
                bootstrap.Modal.getInstance(modalElement);

            if (modal) {

                modal.hide();

            }

            Exam.navigation.go(url);

        }

    },

    // ========================================================
    // Exam Security Module
    // ========================================================

    security: {

        selectionLock: false,
        lastSelectionLog: 0,

        init() {
            // Use arrow wrappers so `this` is always Exam.security and listeners actually fire
            document.addEventListener("visibilitychange", () => this.handleVisibilityChange());
            window.addEventListener("blur", () => this.handleVisibilityChange());
            window.addEventListener("pagehide", () => this.handleVisibilityChange());

            // Use capture phase (true) so we catch before Bootstrap/other handlers stop it
            document.addEventListener("contextmenu", (e) => this.handleRightClick(e), true);
            document.addEventListener("copy", (e) => this.handleCopy(e), true);
            document.addEventListener("cut", (e) => this.handleCut(e), true);
            document.addEventListener("paste", (e) => this.handlePaste(e), true);
            document.addEventListener("keydown", (e) => this.handleKeyDown(e), true);
            document.addEventListener("selectionchange", () => this.handleTextSelection());
            document.addEventListener("fullscreenchange", () => this.handleFullscreenChange());
            // Also listen for PrintScreen key
            window.addEventListener("keyup", (e) => this.handleKeyUp(e), true);
        },

        handleVisibilityChange() {
            // Log if hidden OR if window lost focus (more reliable for tab switch)
            if (document.hidden || !document.hasFocus()) {
                this.logEvent("TAB_SWITCH");
            }
        },

        handleRightClick(event) {
            event.preventDefault();
            event.stopPropagation();
            console.log("Right click blocked.");
            this.logEvent("RIGHT_CLICK");
            return false;
        },

        handleCopy(event) {
            event.preventDefault();
            event.stopPropagation();
            console.log("Copy blocked.");
            this.logEvent("COPY");
            return false;
        },

        handleCut(event) {
            event.preventDefault();
            event.stopPropagation();
            console.log("Cut blocked.");
            this.logEvent("CUT");
            return false;
        },

        handlePaste(event) {
            event.preventDefault();
            event.stopPropagation();
            console.log("Paste blocked.");
            this.logEvent("PASTE");
            return false;
        },

        handleKeyDown(event) {
            // F12
            if (event.key === "F12" || event.keyCode === 123) {
                event.preventDefault();
                console.log("F12 detected.");
                this.logEvent("F12");
            }

            // Ctrl + U
            if (event.ctrlKey && event.key.toLowerCase() === "u") {
                event.preventDefault();
                event.stopPropagation();
                console.log("Ctrl + U detected.");
                this.logEvent("CTRL_U");
                return false;
            }

            // Ctrl + Shift + I / J / C  (all devtools)
            if (event.ctrlKey && event.shiftKey && ["i","j","c"].includes(event.key.toLowerCase())) {
                event.preventDefault();
                event.stopPropagation();
                console.log("Ctrl + Shift + " + event.key.toUpperCase() + " detected.");
                this.logEvent("CTRL_SHIFT_I");
                return false;
            }

            // Ctrl + S, Ctrl + P, Ctrl + C, Ctrl + X, Ctrl + V also count
            if (event.ctrlKey && ["s","p"].includes(event.key.toLowerCase())) {
                event.preventDefault();
                this.logEvent(event.key.toLowerCase() === "s" ? "CTRL_U" : "COPY");
            }
        },

        handleKeyUp(event) {
            // PrintScreen key (often key is PrintScreen or keyCode 44)
            if (event.key === "PrintScreen" || event.keyCode === 44) {
                console.log("PrintScreen detected.");
                this.logEvent("COPY");
            }
        },

        handleTextSelection() {
            const now = Date.now();
            // Throttle to 1.5s to avoid spamming security_events table
            if (now - this.lastSelectionLog < 1500) return;

            const selectedText = window.getSelection().toString().trim();
            if (!selectedText || selectedText.length < 2) {
                return;
            }
            if (this.selectionLock) {
                return;
            }
            this.selectionLock = true;
            this.lastSelectionLog = now;

            console.log("Text selection detected:", selectedText.substring(0,20));
            this.logEvent("TEXT_SELECTION");

            // Delay clearing so COPY event can still fire first, then clear
            setTimeout(() => {
                try { window.getSelection().removeAllRanges(); } catch(e){}
            }, 150);

            setTimeout(() => {
                this.selectionLock = false;
            }, 1200);
        },

        handleFullscreenChange() {
            console.log("Fullscreen Changed, element:", document.fullscreenElement);
            if (document.fullscreenElement) {
                return;
            }
            console.log("Fullscreen exited.");
            this.logEvent("FULLSCREEN_EXIT");
        },

        // LOG EVENT HERE - FIXED WITH keepalive + sendBeacon
        async logEvent(eventType) {
            if (Exam.live.forceSubmitted) {
                return;
            }

            const payload = JSON.stringify({ event: eventType });

            // For visibilitychange / blur, sendBeacon is more reliable than fetch
            try {
                if ((document.hidden || eventType === "TAB_SWITCH" || eventType === "FULLSCREEN_EXIT") && navigator.sendBeacon) {
                    const blob = new Blob([payload], { type: "application/json" });
                    const beaconOk = navigator.sendBeacon("/log-security-event", blob);
                    console.log("sendBeacon for", eventType, "->", beaconOk);
                    // Still do fetch as well for response handling (score update), but don't await blocking
                    if (!beaconOk) {
                        // fallback below
                    }
                }
            } catch (e) {
                console.warn("Beacon failed", e);
            }

            try {
                const response = await fetch("/log-security-event", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: payload,
                    keepalive: true,
                    credentials: "same-origin"
                });

                if (!response.ok) {
                    const txt = await response.text();
                    console.error("Unable to log security event:", eventType, response.status, txt);
                    return;
                }

                const data = await response.json();
                console.log("Logged", eventType, data);
                this.updateSecurityStatus(data);

            } catch (error) {
                console.error("logEvent error for", eventType, error);
            }
        },

        updateSecurityStatus(data) {

            // -----------------------------
            // Update Progress Bar (future)
            // -----------------------------
            const score = data.security_score;

            console.log("Security Score:", score);

            // -----------------------------
            // Warning (70)
            // -----------------------------
            if (data.warning === "warning") {

                Exam.toast.show(
                    "⚠ Warning: You have consumed 30 security points."
                );

            }

            // -----------------------------
            // Critical (40)
            // -----------------------------
            else if (data.warning === "critical") {

                Exam.toast.show(
                    "🚨 Critical Warning: Further violations may automatically submit your exam."
                );

            }

            // -----------------------------
            // Forced Submission
            // -----------------------------
            if (data.force_submit) {

                Exam.modal.showSecurityViolation();

            }

        }

    },

    // ========================================================
    // Toast Notifications
    // ========================================================

    toast: {

        show(message) {

            const status =
                document.getElementById("saveStatus");

            if (!status) return;

            status.innerHTML = message;

            status.classList.add("show");

            setTimeout(() => {

                status.classList.remove("show");

            }, 1000);

        }

    },

    // ========================================================
    // Security Modal Module
    // ========================================================

    modal: {

        showSecurityViolation() {

            // Prevent opening multiple security modals
            if (Exam.securityModalShown) {
                return;
            }

            Exam.securityModalShown = true;

            const modal = new bootstrap.Modal(

                document.getElementById(
                    "securityViolationModal"
                )

            );

            modal.show();

            document
                .getElementById("securityReturnBtn")
                .onclick = function () {

                    console.log("BUTTON CLICKED");

                    Exam.submit.finish(true);

                };

        }

    },

    // ========================================================
    // Submit Exam Module
    // ========================================================

    submit: {

        finish(auto = false) {

            // Forced submission
            if (auto) {

                window.location.href = submitExamURL;

                return;

            }

            Exam.submit.showFinishModal();

        },

        // ==========================================
        // Open Finish Exam Modal
        // ==========================================

        showFinishModal() {

            const modal = new bootstrap.Modal(

                document.getElementById(
                    "finishExamModal"
                )

            );

            modal.show();

            Exam.submit.loadSummary();

        },

        // ==========================================
        // Load Exam Summary
        // ==========================================

        async loadSummary() {

            try {

                const response = await fetch(reviewAnswersURL);

                const reviewData = await response.json();

                const answered =
                    reviewData.filter(q => q.answered).length;

                const total = reviewData.length;

                const remaining = total - answered;

                const percent =
                    Math.round((answered / total) * 100);

                document.getElementById(
                    "finishExamSummary"
                ).innerHTML = `

                    <p>
                        <strong>Answered:</strong>
                        ${answered}
                    </p>

                    <p>
                        <strong>Remaining:</strong>
                        ${remaining}
                    </p>

                    <p>
                        <strong>Completion:</strong>
                        ${percent}%
                    </p>

                `;

            }

            catch(error){

                console.error(error);

            }

        },

        // ==========================================
        // Final Submission
        // ==========================================

        confirmFinish() {

            if (Exam.isSubmitting) {

                return;

            }

            Exam.isSubmitting = true;

            window.location.href = submitExamURL;

        }

    },

    // ============================================
    // ===== Student Live SSE =====================
    // ============================================

    live: {

        stream: null,

        forceSubmitted: false,

        init() {

            console.log("Lightweight force-submit checker started.");

            setInterval(async () => {

                try {

                    const response = await fetch("/exam/check-force-submit");

                    const data = await response.json();

                    if (data.force_submit && !this.forceSubmitted) {

                        console.log("FORCE SUBMIT DETECTED");

                        Exam.live.forceSubmitted = true;

                        // Stop countdown
                        if (Exam.timer.interval) {
                            clearInterval(Exam.timer.interval);
                        }

                        // Prevent any further submission
                        Exam.isSubmitting = true;

                        // Disable answer choices
                        document.querySelectorAll(".option-btn").forEach(btn => {
                            btn.disabled = true;
                        });

                        // Disable every button except the dashboard button
                        document.querySelectorAll("button").forEach(btn => {

                            if (btn.id !== "backToDashboardBtn") {
                                btn.disabled = true;
                            }

                        });

                        const modal = new bootstrap.Modal(
                            document.getElementById("forceSubmitModal"),
                            {
                                backdrop: "static",
                                keyboard: false
                            }
                        );

                        modal.show();

                    }

                } catch (err) {

                    console.error("Force-submit check failed:", err);

                }

            }, 5000);

        }

    }
};

// ============================================================
// Start the Exam Engine
// ============================================================

document.addEventListener("DOMContentLoaded", () => {

    console.log("Exam Engine Loaded");

    Exam.init();

});