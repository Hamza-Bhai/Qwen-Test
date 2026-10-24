**cd frontend**

**In teeno commands ki detail:**

1. **`npm run typecheck`**
   * **Kya karti hai:** Yeh command aapke TypeScript code ki "types" check karti hai.
   * **Fayda:** Yeh ensure karti hai ke code mein koi type-related errors (jaise string ki jagah number pass karna, ya missing properties) toh nahi hain. Yeh code ko compile nahi karti, sirf check karti hai taake runtime par achanak crash na ho.
2. **`npm run lint`**
   * **Kya karti hai:** Yeh command code ki quality, formatting, aur best practices check karti hai (usually ESLint ke through).
   * **Fayda:** Yeh batati hai ke code mein koi unnecessary variables, syntax mistakes, ya aisi cheezein toh nahi hain jo code ko slow ya confusing bana sakti hain. Is se poore project ka code clean aur consistent rehta hai.
3. **`npm run build`**
   * **Kya karti hai:** Yeh command aapke poore source code (TypeScript/React etc.) ko compile, minify (chota), aur bundle kar deti hai.
   * **Fayda:** Yeh aapke project ka **production-ready** (live website/server par chalane ke liye tayar) version bana deti hai. Iska output aam taur par `dist`, `build`, ya `.next` jaise folder mein save hota hai, jise aap deploy kar sakte hain.
4. npm run start
