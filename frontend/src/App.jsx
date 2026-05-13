import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
// import LCAForm from './components/LCAForm'
import LCADashboard from './components/LCADashboard'
function App() {
  const [count, setCount] = useState(0)
  
  return (
    <>
     <LCADashboard/>
    </>
  )
}

export default App