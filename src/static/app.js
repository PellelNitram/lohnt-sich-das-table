// Progressive enhancement: the table is fully server-rendered; this only adds sorting and search.
(() => {
  const table = document.getElementById("tbl");
  const search = document.getElementById("search");
  if (!table || !search) return;

  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.querySelectorAll("tr[data-profession]"));
  const headers = Array.from(table.tHead.querySelectorAll("th[data-key]"));
  let sortKey = "date";
  let sortDir = -1;

  function compare(a, b) {
    if (sortKey === "profession") {
      return sortDir * a.dataset.profession.localeCompare(b.dataset.profession, "de");
    }
    const va = a.dataset[sortKey], vb = b.dataset[sortKey];
    if (va === "" && vb === "") return 0;
    if (va === "") return 1; // empty values always last
    if (vb === "") return -1;
    if (sortKey === "date") return sortDir * va.localeCompare(vb);
    return sortDir * (parseFloat(va) - parseFloat(vb));
  }

  function render() {
    const q = search.value.toLowerCase().trim();
    rows.sort(compare).forEach(tr => {
      tr.hidden = q !== "" && !tr.dataset.profession.includes(q);
      tbody.appendChild(tr);
    });
    headers.forEach(th => {
      const on = th.dataset.key === sortKey;
      th.classList.toggle("sorted", on);
      th.querySelector(".arrow").textContent = on ? (sortDir === -1 ? "▼" : "▲") : "";
    });
  }

  headers.forEach(th => th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (key === sortKey) sortDir *= -1;
    else { sortKey = key; sortDir = key === "profession" ? 1 : -1; }
    render();
  }));
  search.addEventListener("input", render);
})();
