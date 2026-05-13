const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const bodyParser = require('body-parser');
const axios = require('axios'); 
const path = require('path'); // ADDED: Required to find the React build folder
const Project = require('./models/Project');
const CompanyDefaults = require('./models/CompanyDefaults'); 

require('dotenv').config();

const app = express();

app.use(cors());
app.use(bodyParser.json());

// ------------------------------------------
// 1. MONGODB CONNECTION
// ------------------------------------------
mongoose.connect(process.env.MONGO_URI)
  .then(() => console.log('MongoDB connected'))
  .catch(err => console.error(err)); 

// ------------------------------------------
// 2. DATA PERSISTENCE & DEFAULTS ROUTES
// ------------------------------------------

// A. Endpoint to SAVE a new simulation project
app.post('/api/project', async (req, res) => {
  try {
    const project = new Project(req.body);
    await project.save();
    res.json(project);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

// B. Endpoint to SET/UPDATE company-specific defaults
app.post('/api/defaults/:companyId', async (req, res) => {
  const companyId = req.params.companyId;
  try {
    const defaults = await CompanyDefaults.findOneAndUpdate(
      { companyId: companyId },
      req.body,
      { new: true, upsert: true, setDefaultsOnInsert: true }
    );
    res.json(defaults);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

// C. Endpoint to FETCH company-specific defaults
app.get('/api/defaults/:companyId', async (req, res) => {
  const companyId = req.params.companyId;
  try {
    const defaults = await CompanyDefaults.findOne({ companyId: companyId });
    res.json(defaults || {}); 
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

// ------------------------------------------
// 3. API GATEWAY ROUTES (Forward to Python)
// ------------------------------------------

// ADDED: Use environment variable for Python AI, fallback to localhost for local dev
const PYTHON_URL = process.env.PYTHON_URL || 'http://localhost:8000';

// A. AI Imputation (AI Auto-Fill)
app.post('/api/impute', async (req, res) => {
    try {
        const pythonResponse = await axios.post(`${PYTHON_URL}/impute`, req.body);
        res.json(pythonResponse.data);
    } catch (error) {
        console.error('Error calling Python AI model for imputation:', error.response ? error.response.data : error.message);
        res.status(500).json({ error: 'Failed to impute data. Check Python server console.' });
    }
});

// B. Simulation Run
app.post('/api/simulate', async (req, res) => {
    try {
        const pythonResponse = await axios.post(`${PYTHON_URL}/simulate`, req.body);
        res.json(pythonResponse.data);
    } catch (error) {
        console.error('Error calling Python AI model for simulation:', error.response ? error.response.data : error.message);
        res.status(500).json({ error: 'Failed to run simulation. Check Python server console.' });
    }
});

// C. PDF Report Generation
app.post('/api/report', async (req, res) => {
    try {
        const pythonResponse = await axios.post(`${PYTHON_URL}/report`, req.body, {
            responseType: 'arraybuffer' 
        });

        res.setHeader('Content-Type', 'application/pdf');
        res.setHeader('Content-Disposition', 'attachment; filename=lca_report.pdf');
        res.send(pythonResponse.data);

    } catch (error) {
        console.error('Error generating report:', error.response ? error.response.data : error.message);
        res.status(500).json({ error: 'Failed to generate report. Check Python server console.' });
    }
});

// ------------------------------------------
// 4. REACT FRONTEND HOSTING (Added for combined deployment)
// ------------------------------------------

// Tell Express to serve the static files from your React build
app.use(express.static(path.join(__dirname, '../frontend/dist')));

// Catch-all route: If the user requests a route that isn't an API, send the React frontend
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, '../frontend/dist/index.html'));
});

// ------------------------------------------
// SERVER START
// ------------------------------------------
const PORT = process.env.PORT || 5000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));