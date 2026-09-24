/* HIS schema expected by the app (same tables/columns as the DateBaseHIS.xlsx export).
   Run once as an administrator:  sqlcmd -S localhost -U sa -i docs/sqlserver/schema.sql
   If your HIS already has these tables, skip the CREATE TABLE part and only create the login. */

IF DB_ID('HIS') IS NULL CREATE DATABASE HIS;
GO
USE HIS;
GO

CREATE SEQUENCE dbo.SeqConsecutivoIngreso AS BIGINT START WITH 1 INCREMENT BY 1;
GO

CREATE TABLE dbo.Paciente (
    IdPaciente      BIGINT        NOT NULL PRIMARY KEY,
    TipoDocumento   VARCHAR(4)    NOT NULL,
    NombrePaciente  NVARCHAR(120) NOT NULL,
    FechaNacimiento DATE          NOT NULL,
    Sexo            NVARCHAR(20)  NOT NULL,
    Asegurador      NVARCHAR(160) NOT NULL,
    Regimen         NVARCHAR(60)  NOT NULL,
    Departamento    NVARCHAR(80)  NOT NULL,
    Municipio       NVARCHAR(80)  NOT NULL,
    Zona            NVARCHAR(20)  NOT NULL
);

CREATE TABLE dbo.Triage (
    OidTriage              BIGINT IDENTITY(1,1) PRIMARY KEY,
    IdPaciente2            BIGINT        NOT NULL REFERENCES dbo.Paciente(IdPaciente),
    FechaTriage            DATETIME2(0)  NOT NULL,
    MotivoConsulta         NVARCHAR(300) NOT NULL,
    TensionArterial        VARCHAR(7)    NULL,
    FrecuenciaCardiaca     DECIMAL(5,1)  NULL,
    FrecuenciaRespiratoria DECIMAL(5,1)  NULL,
    Temperatura            DECIMAL(4,1)  NULL,
    CodigoTriage           INT           NULL,
    ClasificacionTriage    NVARCHAR(120) NOT NULL
);

CREATE TABLE dbo.Ingresos (
    OidIngreso           BIGINT IDENTITY(1,1) PRIMARY KEY,
    ConsecutivoIngreso   BIGINT        NOT NULL DEFAULT (NEXT VALUE FOR dbo.SeqConsecutivoIngreso),
    IdPaciente           BIGINT        NOT NULL REFERENCES dbo.Paciente(IdPaciente),
    ClaseIngreso         NVARCHAR(60)  NOT NULL,
    ViaIngreso           NVARCHAR(60)  NOT NULL,
    TipoRiesgo           NVARCHAR(80)  NOT NULL,
    FechaIngreso         DATETIME2(0)  NOT NULL,
    FechaHospitalizacion DATETIME2(0)  NULL,
    OidTriageA           BIGINT        NULL REFERENCES dbo.Triage(OidTriage),
    CodigoCama           VARCHAR(20)   NOT NULL,
    NombreCama           NVARCHAR(80)  NOT NULL,
    NombreGrupoCama      NVARCHAR(80)  NOT NULL,
    NombreSubgrupoCama   NVARCHAR(80)  NOT NULL,
    CodigoDiagnostico    VARCHAR(6)    NOT NULL,
    NombreDiagnostico    NVARCHAR(200) NOT NULL,
    CONSTRAINT CK_Ingresos_Hospitalizacion CHECK (FechaHospitalizacion IS NULL OR FechaHospitalizacion >= FechaIngreso)
);

CREATE TABLE dbo.Atencion (
    OidIngreso    BIGINT       NOT NULL PRIMARY KEY REFERENCES dbo.Ingresos(OidIngreso),
    FechaAtencion DATETIME2(0) NOT NULL
);

CREATE TABLE dbo.Servicios (
    OidS               BIGINT IDENTITY(1,1) PRIMARY KEY,
    OidIngreso         BIGINT        NOT NULL REFERENCES dbo.Ingresos(OidIngreso),
    CodigoServicio     VARCHAR(20)   NOT NULL,
    NombreServicio     NVARCHAR(200) NOT NULL,
    Cantidad           INT           NOT NULL CHECK (Cantidad > 0),
    FechaPrestacion    DATETIME2(0)  NOT NULL,
    CodigoAreaServicio INT           NULL,
    AreaServicio       NVARCHAR(120) NOT NULL,
    Especialidad       NVARCHAR(120) NOT NULL
);

CREATE TABLE dbo.MedicamentoInsumo (
    OidMI           BIGINT IDENTITY(1,1) PRIMARY KEY,
    OidIngreso      BIGINT        NOT NULL REFERENCES dbo.Ingresos(OidIngreso),
    CodigoServicio  VARCHAR(20)   NOT NULL,
    NombreServicio  NVARCHAR(200) NOT NULL,
    Cantidad        INT           NOT NULL CHECK (Cantidad > 0),
    FechaPrestacion DATETIME2(0)  NOT NULL,
    AreaServicio    NVARCHAR(120) NOT NULL,
    Especialidad    NVARCHAR(120) NOT NULL
);

CREATE TABLE dbo.ProgramacionCirugia (
    ConsecutivoProgramacion BIGINT      NOT NULL PRIMARY KEY,
    IdPaciente              BIGINT      NOT NULL,
    OidIngreso              BIGINT      NULL REFERENCES dbo.Ingresos(OidIngreso),
    CodigoServicio          VARCHAR(20) NOT NULL
);
GO

/* Least-privilege login for the app: it can read and INSERT, never UPDATE / DELETE / ALTER.
   Replace the password, then set SQLSERVER_USER / SQLSERVER_PASSWORD in .env. */
CREATE LOGIN hospital_app WITH PASSWORD = 'REPLACE-with-a-long-random-password-1!';
CREATE USER hospital_app FOR LOGIN hospital_app;
GRANT SELECT ON dbo.Paciente TO hospital_app;
GRANT INSERT ON dbo.Paciente TO hospital_app;
GRANT INSERT ON dbo.Triage TO hospital_app;
GRANT INSERT ON dbo.Ingresos TO hospital_app;
GRANT INSERT ON dbo.Atencion TO hospital_app;
GRANT INSERT ON dbo.Servicios TO hospital_app;
GRANT INSERT ON dbo.MedicamentoInsumo TO hospital_app;
DENY UPDATE, DELETE, ALTER ON SCHEMA::dbo TO hospital_app;
GO
