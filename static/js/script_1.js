document.addEventListener("DOMContentLoaded", function() {

    // --- Flash Messages Auto-Hide ---
    const flashes = document.querySelectorAll(".flash");
    flashes.forEach(flash => {
        setTimeout(() => {
            flash.style.transition = "opacity 0.5s ease, transform 0.5s ease";
            flash.style.opacity = "0";
            flash.style.transform = "translateY(-10px)";
            setTimeout(() => flash.remove(), 500);
        }, 3000);
    });

    // --- Bootstrap Tooltips ---
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    tooltipTriggerList.map(function (el) {
        return new bootstrap.Tooltip(el);
    });

    // =========================
    // BULK DELETE (FIXED)
    // =========================

    const bulkForm = document.getElementById('bulk-delete-form');
    const selectAll = document.getElementById('select-all');
    const deleteBtn = document.getElementById('delete-selected');
    const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
    const modalBodyText = document.getElementById('modal-body-text');

    function updateDeleteButtonText() {
        const count = document.querySelectorAll('input[name="student_ids"]:checked').length;
        if (deleteBtn) {
            deleteBtn.textContent = count > 0
                ? `Delete Selected (${count})`
                : 'Delete Selected';
        }
    }

    if (selectAll) {
        selectAll.addEventListener('change', function() {
            let checkboxes = document.querySelectorAll('input[name="student_ids"]');
            checkboxes.forEach(cb => cb.checked = this.checked);
            updateDeleteButtonText();
        });
    }

    const checkboxes = document.querySelectorAll('input[name="student_ids"]');
    checkboxes.forEach(cb => cb.addEventListener('change', updateDeleteButtonText));

    if (deleteBtn && bulkForm) {
        deleteBtn.addEventListener('click', function() {

            const count = document.querySelectorAll('input[name="student_ids"]:checked').length;

            if (count === 0) {
                alert('Please select at least one student to delete.');
                return;
            }

            if (modalBodyText) {
                modalBodyText.textContent =
                    `Are you sure you want to delete ${count} student(s)? This action cannot be undone.`;
            }
        });

        if (confirmDeleteBtn) {
            confirmDeleteBtn.addEventListener('click', function() {
                bulkForm.submit();
            });
        }
    }

    // =========================
    // EDIT MODAL
    // =========================

    let editModal = null;
    const editModalEl = document.getElementById("editModal");

    if (editModalEl) {
        editModal = new bootstrap.Modal(editModalEl);
    }

    window.openEditModal = function(id, question, a, b, c, d, correct, points) {

        if (!editModal) return;

        document.getElementById("edit_id").value = id;
        document.getElementById("edit_question").value = question;

        document.getElementById("edit_choice_a").value = a;
        document.getElementById("edit_choice_b").value = b;
        document.getElementById("edit_choice_c").value = c;
        document.getElementById("edit_choice_d").value = d;

        document.getElementById("edit_correct_answer").value = correct;
        document.getElementById("edit_points").value = points;

        editModal.show();
    };

    // =========================
    // SAVE EDIT (AJAX)
    // =========================

    window.saveEdit = function() {

        let id = document.getElementById("edit_id").value;

        fetch(`/edit-question/${id}`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                question_text: document.getElementById("edit_question").value,
                choice_a: document.getElementById("edit_choice_a").value,
                choice_b: document.getElementById("edit_choice_b").value,
                choice_c: document.getElementById("edit_choice_c").value,
                choice_d: document.getElementById("edit_choice_d").value,
                correct_answer: document.getElementById("edit_correct_answer").value,
                points: document.getElementById("edit_points").value
            })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                location.reload();
            }
        });
    };
});

// =========================
// REQUEST EXAM
// =========================

window.requestExam = function(examId) {

    fetch(`/request_exam/${examId}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        }
    })
    .then(response => response.json())
    .then(data => {

        alert(data.message);

        if (data.success) {
            location.reload();
        }

    })
    .catch(error => {
        console.error(error);
        alert("Request failed.");
    });

};