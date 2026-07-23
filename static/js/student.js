// Add simple row hover effect or click
document.addEventListener('DOMContentLoaded', () => {
    const rows = document.querySelectorAll('.grades-table tbody tr');
    rows.forEach(row => {
        row.addEventListener('click', () => {
            alert(`Subject: ${row.cells[0].innerText}\nGrade: ${row.cells[1].innerText}\nRemarks: ${row.cells[2].innerText}`);
        });
    });
});
