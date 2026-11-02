// "use client";

// import { useRouter } from "next/navigation";

// export default function AppNavigation() {
//   const router = useRouter();

//   const handleBack = () => {
//     if (window.history.length > 1) {
//       router.back();
//     } else {
//       router.replace("/dashboard?tab=genie");
//     }
//   };

//   const handleSignOut = () => {
//     // Clear the authentication values used by Ryze
//     localStorage.removeItem("ryze_token");
//     localStorage.removeItem("ryze_user");

//     // Return to login and prevent navigating back into the app
//     router.replace("/login");
//   };

//   return (
//     <div className="flex items-center gap-2">
//       <button
//         type="button"
//         onClick={handleBack}
//         className="flex items-center gap-2 rounded-lg border
//                    border-purple-200 px-3 py-2 text-sm
//                    text-purple-100 hover:bg-purple-800"
//         aria-label="Go to previous page"
//       >
//         <span aria-hidden="true">←</span>
//         Back
//       </button>

//       <button
//         type="button"
//         onClick={handleSignOut}
//         className="flex items-center gap-2 rounded-lg
//                    bg-purple-700 px-3 py-2 text-sm
//                    text-white hover:bg-purple-600"
//       >
//         <span aria-hidden="true">↪</span>
//         Sign Out
//       </button>
//     </div>
//   );
// }
"use client";

import { useRouter } from "next/navigation";

export default function AppNavigation() {
  const router = useRouter();

  function handleBack() {
    if (window.history.length > 1) {
      router.back();
    } else {
      router.replace("/dashboard");
    }
  }

  return (
    <button
      type="button"
      onClick={handleBack}
      className="app-navigation__logout"
      aria-label="Go to previous page"
      title="Go to previous page"
    >
      <span aria-hidden="true">←</span> Back
    </button>
  );
}