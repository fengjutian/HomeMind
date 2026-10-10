const languageButton = document.querySelector(".lang-toggle");
const translatable = document.querySelectorAll("[data-zh][data-en]");
let language = localStorage.getItem("homemind-language") || "zh";

function applyLanguage(nextLanguage) {
  language = nextLanguage;
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.title = language === "zh" ? "HomeMind — 懂每一位家人的 AI" : "HomeMind — AI that knows your home";
  translatable.forEach((element) => {
    element.innerHTML = element.dataset[language];
  });
  languageButton.textContent = language === "zh" ? "EN" : "中";
  localStorage.setItem("homemind-language", language);
}

languageButton.addEventListener("click", () => applyLanguage(language === "zh" ? "en" : "zh"));
applyLanguage(language);

document.querySelector("#year").textContent = new Date().getFullYear();

document.querySelector(".copy-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  await navigator.clipboard.writeText(button.dataset.copy);
  const original = language === "zh" ? "复制" : "Copy";
  button.textContent = language === "zh" ? "已复制" : "Copied";
  window.setTimeout(() => { button.textContent = original; }, 1600);
});

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) entry.target.classList.add("is-visible");
  });
}, { threshold: 0.12 });

document.querySelectorAll(".manifesto article, .feature, .steps li").forEach((element) => observer.observe(element));
