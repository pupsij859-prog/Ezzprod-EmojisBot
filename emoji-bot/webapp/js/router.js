// Тонкая прослойка, чтобы экраны могли переключать вкладки, не импортируя main.js (без циклов).
let handler = () => {};

export const setTabHandler = (fn) => { handler = fn; };
export const goTab = (name) => handler(name);
