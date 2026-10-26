document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("button[title]").forEach((button) => {
    if (!button.hasAttribute("aria-label"))
      button.setAttribute("aria-label", button.title);
  });
  document
    .querySelectorAll("#upcoming-trainings, #past-trainings")
    .forEach((body) => {
      if (!body.children.length) {
        const row = body.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 4;
        cell.className = "empty-state";
        const title = document.createElement("strong");
        title.textContent =
          body.id === "upcoming-trainings"
            ? "Впереди новая игра"
            : "История команды начинается здесь";
        const description = document.createElement("p");
        description.textContent =
          body.id === "upcoming-trainings"
            ? "Добавьте тренировку или игру, чтобы открыть запись участникам."
            : "Завершённые тренировки появятся в этом разделе.";
        cell.append(title, description);
      }
    });
  const search = document.getElementById("roster-search");
  if (search)
    search.addEventListener("input", () => {
      const query = search.value.trim().toLocaleLowerCase("ru");
      document.querySelectorAll("tr[data-user-id]").forEach((row) => {
        const cells = row.querySelectorAll("td");
        const name = [cells[1]?.textContent, cells[2]?.textContent]
          .join(" ")
          .toLocaleLowerCase("ru");
        row.hidden = !name.includes(query);
      });
    });
});
