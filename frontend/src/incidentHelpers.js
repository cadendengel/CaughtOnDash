// Data and formatting for the incident report, kept out of the component
// file so React fast refresh can hot-swap it (it only handles files that
// export components alone).

export const OTHER_PARTY_FIELDS = [
  ['name', 'Name'],
  ['phone', 'Phone'],
  ['email', 'Email'],
  ['address', 'Address'],
  ['drivers_license', "Driver's licence"],
  ['vehicle_plate', 'Plate'],
  ['vehicle_description', 'Vehicle'],
  ['insurer', 'Insurer'],
  ['policy_number', 'Policy number'],
  ['claim_number', 'Claim number'],
  ['police_report_number', 'Police report number'],
  ['notes', 'Notes about them'],
]

// Lookups from the Vehicle OSINT collection: vehicle- and carrier-focused only.
// None of these is called by the site; each opens in the owner's browser, where
// the sites' own CAPTCHAs and terms apply. Plate-to-owner identity is protected
// by the DPPA -- the route to the driver is the rental or fleet company,
// through the owner's insurer or the police.
export const LOOKUPS = [
  {
    group: 'Plate',
    note: 'Run these by hand. They report the vehicle, not its owner.',
    links: [
      ['FindByPlate', 'https://findbyplate.com/'],
      ['FaxVin plate lookup', 'https://www.faxvin.com/license-plate-lookup'],
      ['VehicleHistory plate search', 'https://www.vehiclehistory.com/license-plate-search'],
      ['EpicVin plate lookup', 'https://epicvin.com/license-plate-lookup'],
    ],
  },
  {
    group: 'VIN',
    links: [
      ['NHTSA VIN decoder', 'https://www.nhtsa.gov/vin-decoder'],
      ['NHTSA recalls', 'https://www.nhtsa.gov/recalls'],
      ['BigRig VIN (commercial)', 'https://bigrigvin.com/'],
    ],
  },
  {
    group: 'Commercial vehicles',
    note: 'Commercial trucks must display a USDOT number; the FMCSA record names the carrier.',
    links: [
      ['FMCSA company snapshot', 'https://safer.fmcsa.dot.gov/CompanySnapshot.aspx'],
      ['Commercial Truck Trader', 'https://www.commercialtrucktrader.com/'],
    ],
  },
]

export const mapLinks = (lat, lon) => [
  ['Google Maps', `https://www.google.com/maps?q=${lat},${lon}`],
  ['Mapillary street imagery', `https://www.mapillary.com/app/?lat=${lat}&lng=${lon}&z=17`],
]

export const formatSeconds = (seconds) => {
  const total = Number(seconds) || 0
  const minutes = Math.floor(total / 60)
  const rest = (total - minutes * 60).toFixed(1).padStart(4, '0')
  return `${minutes}:${rest}`
}

export const formatPlace = (lat, lon) => {
  if (lat == null || lon == null) return null
  const ns = lat >= 0 ? 'N' : 'S'
  const ew = lon >= 0 ? 'E' : 'W'
  return `${Math.abs(lat).toFixed(5)}°${ns} ${Math.abs(lon).toFixed(5)}°${ew}`
}

export const formatClock = (iso) => (iso ? iso.replace('T', ' ') : null)

// Only the latest analysis attempt's images belong to the current findings;
// earlier attempts' images are kept on the server as evidence but not shown.
export const latestAttempt = (artifacts) => {
  const attempts = artifacts
    .filter((a) => a.kind !== 'photo' && a.attempt_number != null)
    .map((a) => a.attempt_number)
  return attempts.length ? Math.max(...attempts) : null
}
